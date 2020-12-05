import base64
import mysql.connector as mysql
import datetime
import pandas as pd
import time
import requests

import json
from web3 import Web3 # https://web3py.readthedocs.io/en/stable/contracts.html#contract-functions

#######
## INIT
#######

# load api key
secret = {}
with open('secret_staging.txt') as f:
    lines = f.readlines()
    for line in lines:
        secret[line.split("=")[0]] = line.split("=")[1].replace("\n","")

# init web3
infura_url = secret['INFURAURL1']
web3 = Web3(Web3.HTTPProvider(infura_url))
print(f"Connected to infura: {infura_url}")

# load abi
with open('abi.json') as f:
    abi = json.load(f)

# load bytecode
with open('bytecode.txt', 'r') as file:
    bytecode = file.read().replace('\n', '')

# load contract address
contract_address = secret['CONTRACTADDRESS']

###########################
## GET USER:WALLET MAPPINGS
###########################

# convert rewards pending twitter handles to wallet ids
db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
cursor = db.cursor()
query = f'SELECT twitter_handle, ethereum_address FROM users;'
cursor.execute(query)
records = cursor.fetchall()
cursor.close()
db.close()
# convert to dataframe
users = []
for record in records:
    users.append(dict(zip(['twitter_handle', 'ethereum_address'], record)))
# convert to dataframe and lowercase handle
users = pd.DataFrame(users)
users['twitter_handle'] = users['twitter_handle'].str.lower()

# create dicts
user_wallets = dict(zip(list(users['twitter_handle']), users['ethereum_address']))
wallets_users = dict(zip(list(users['ethereum_address']), users['twitter_handle']))

# create list
user_list = list(user_wallets.keys())

##################################################
### get all rewards for this campaign as dataframe
##################################################
db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
cursor = db.cursor()
query = f'SELECT campaign_id, twitter_handle FROM rewards WHERE blockchain_write_time is NULL;'
cursor.execute(query)
records_rewards = cursor.fetchall()
cursor.close()
db.close()
# convert to dataframe
rewards = []
for record in records_rewards:
    rewards.append(dict(zip(['campaign_id', 'twitter_handle'], record)))
df = pd.DataFrame(rewards)
print(f"DEGBUG: LEN DF {len(df)}")

#######################
## GET ACTIVE CAMPAIGNS
#######################

# get all campaigns with twitter handle so we can get twitter link
db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
cursor = db.cursor()
query = f'SELECT campaign_id, manager_ethereum_address, maximum_rewards, campaign_type, twitter_status_id FROM campaigns;'
cursor.execute(query)
records = cursor.fetchall()
print(f"{len(records)} campaigns found")
cursor.close()
db.close()

columns = ['campaign_id', 'manager_ethereum_address', 'maximum_rewards', 'campaign_type', 'twitter_status_id']

campaigns = []
for record in records:
    res = dict(zip(columns, record))

    # check how many rewards claimed for this campaign
    db = mysql.connect(host=secret['DBHOST'], user=secret['DBUSER'], passwd=secret['DBPASS'], database=secret['DBTABLE'])
    cursor = db.cursor()
    manager_ethereum_address = res['manager_ethereum_address']
    campaign_id = res['campaign_id']
    query = f'SELECT * FROM rewards where campaign_id = "{campaign_id}" and manager_ethereum_address = "{manager_ethereum_address}";'
    cursor.execute(query)
    records_rewards = cursor.fetchall()
    print(f"campaign #{res['campaign_id']} has {len(records_rewards)} rewards claimed, {res['maximum_rewards'] - len(records_rewards)} remaining")
    cursor.close()
    db.close()

    # calculate rewards remaining
    res['rewards_remaining'] = res['maximum_rewards'] - len(records_rewards)

    # only keep active campaigns
    if res['rewards_remaining'] > 0:
        campaigns.append(res)

print(f"{len(campaigns)} active campaigns found")

for campaign in campaigns:

    print(f"Begin allocating rewards for campaign {campaign['campaign_id']} with manager address {campaign['manager_ethereum_address']}")

    ###################################################
    ### GET LIST OF WALLETS TO REWARD FOR THIS CAMPAIGN
    ###################################################

    # list of handles to reward
    rewards_pending = list(df[df['campaign_id'] == campaign['campaign_id']]['twitter_handle'])

    # convert from twitter handles to wallets
    rewards_pending = [user_wallets[r] for r in rewards_pending]

    print(f"{len(rewards_pending)} rewards_pending")
    print(rewards_pending)

    # get gas price from ethgasstation.info
    gasPrice = 25
    try:
        headers = {'User-Agent': 'blah',}
        response = requests.get('https://ethgasstation.info/json/ethgasAPI.json', headers=headers)
        gasPrice = int(response.json()['average']/10)
        print(f"GasPrice fetched from ethgasstation.info {gasPrice}")
    except Exception as e:
        print("ERROR FETCHING GAS PRICE - using default of 25")
        print(e)

    for i, reward_pending in enumerate(rewards_pending):
        print(f"Creating SC tx {i+1}/{len(rewards_pending)} to reward {reward_pending}")

        ###################################
        ### WRITE REWARDS TO SMART CONTRACT
        ###################################

        contract = web3.eth.contract(abi=abi, bytecode=bytecode)

        tx = contract.functions.rewardAddresses(campaign['manager_ethereum_address'], campaign['campaign_id'], [reward_pending]).buildTransaction(
            {'gas':250000, # 0.0029056 vs 0.00062844 0.000000001 ETH
             'gasPrice': web3.toWei('24', 'gwei'),
             'from': secret['ETHBACKENDPUBLIC'],
             'to': contract_address,
             'nonce': web3.eth.getTransactionCount(secret['ETHBACKENDPUBLIC'])
            })

        signed_txn = web3.eth.account.signTransaction(tx, private_key=secret['ETHBACKENDPRIVATE'])
        tx_hash = web3.eth.sendRawTransaction(signed_txn.rawTransaction)
        print(f"TX hash: {tx_hash.hex()}")
        receipt = web3.eth.waitForTransactionReceipt(tx_hash, timeout=600)
        print(receipt)

        print(f"LOGGING: {tx_hash}, rewarding: {reward_pending}, manager: {campaign['manager_ethereum_address']}, ")

        if receipt['status'] == 1:
            print(f"SUCCESS writing rewards to SC for campaign {campaign['campaign_id']} tx: {tx_hash.hex()}")

            # update blockchain_write_time
            db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
            cursor = db.cursor()
            query = "UPDATE rewards SET blockchain_write_time=%s, blockchain_write_tx_hash=%s, gas_used=%s, blockchain_write_tx_status=%s WHERE campaign_id=%s AND manager_ethereum_address=%s AND twitter_handle=%s;"
            values = (str(datetime.datetime.now()).split('.')[0], str(tx_hash.hex()), receipt['gasUsed'], 1, str(campaign['campaign_id']), campaign['manager_ethereum_address'], wallets_users[reward_pending])
            cursor.execute(query, values)
            db.commit()
            print(cursor.rowcount, "record updated")
            cursor.close()
            db.close()  
        else:
            print(f"ERROR FAIL writing rewards to SC for campaign {campaign['campaign_id']} tx: {tx_hash.hex()}")