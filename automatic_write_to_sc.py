#!/usr/bin/env python
# coding: utf-8

import base64
import mysql.connector as mysql
import datetime
import pandas as pd
import time
import requests
import traceback

import json
from web3 import Web3 # https://web3py.readthedocs.io/en/stable/contracts.html#contract-functions

#######
## INIT
#######

# load api key
secret = {}
with open('secret.txt') as f:
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

while True:
    try:
        print(f"{datetime.datetime.now()} running SC write process")

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

        ###########################
        ### get all pending rewards
        ###########################
        db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
        cursor = db.cursor()
        query = f'SELECT campaign_id, twitter_handle, manager_ethereum_address, campaign_id FROM rewards WHERE blockchain_write_time is NULL;'
        cursor.execute(query)
        records_rewards = cursor.fetchall()
        cursor.close()
        db.close()
        # convert to dataframe
        rewards = []
        for record in records_rewards:
            rewards.append(dict(zip(['campaign_id', 'twitter_handle', 'manager_ethereum_address', 'campaign_id'], record)))
        df = pd.DataFrame(rewards)
        print(f"DEGBUG: LEN DF {len(df)}")

        # merge ethereum address
        df = pd.merge(df, users, left_on='twitter_handle', right_on='twitter_handle', how='left')

        null_rows = len(df[df['ethereum_address'].isnull()])
        if null_rows > 0:
            print(f"{null_rows} rows with null ethereum address")
            df = df[~df['ethereum_address'].isnull()]

        for i, row in enumerate(df.values):
            row_dict = dict(zip(df.columns, row))
            print(f"Processing reward {i+1}/{len(df)}")
            print(f"{row_dict}")

            ###################################
            ### WRITE REWARDS TO SMART CONTRACT
            ###################################

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

            # if gas price high, sleep 2 minutes and try again
            # TODO: make this keep retrying until waited max 15 minutes
            if gasPrice > 50:
                time.sleep(120) 
                try:
                    headers = {'User-Agent': 'blah',}
                    response = requests.get('https://ethgasstation.info/json/ethgasAPI.json', headers=headers)
                    gasPrice = int(response.json()['average']/10)
                    print(f"GasPrice fetched from ethgasstation.info {gasPrice}")
                except Exception as e:
                    print("ERROR FETCHING GAS PRICE - using default of 25")
                    print(e)

            contract = web3.eth.contract(abi=abi, bytecode=bytecode)

            tx = contract.functions.rewardAddresses(Web3.toChecksumAddress(row_dict['manager_ethereum_address']), row_dict['campaign_id'], [Web3.toChecksumAddress(row_dict['ethereum_address'])]).buildTransaction(
                {'gas':250000,
                 'gasPrice': web3.toWei(gasPrice, 'gwei'),
                 'from': secret['ETHBACKENDPUBLIC'],
                 'to': contract_address,
                 'nonce': web3.eth.getTransactionCount(secret['ETHBACKENDPUBLIC'])
                })

            signed_txn = web3.eth.account.signTransaction(tx, private_key=secret['ETHBACKENDPRIVATE'])
            tx_hash = web3.eth.sendRawTransaction(signed_txn.rawTransaction)
            print(f"TX hash: {tx_hash.hex()}")
            receipt = web3.eth.waitForTransactionReceipt(tx_hash, timeout=3600)
            print(receipt)

            if receipt['status'] == 1:
                print(f"SUCCESS writing rewards to SC tx: {tx_hash.hex()}")

                # update blockchain_write_time
                db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
                cursor = db.cursor()
                query = "UPDATE rewards SET blockchain_write_time=%s, blockchain_write_tx_hash=%s, gas_used=%s, blockchain_write_tx_status=%s WHERE campaign_id=%s AND manager_ethereum_address=%s AND twitter_handle=%s;"
                values = (str(datetime.datetime.now()).split('.')[0], str(tx_hash.hex()), receipt['gasUsed'], 1, str(row_dict['campaign_id']), row_dict['manager_ethereum_address'], row_dict['twitter_handle'])
                cursor.execute(query, values)
                db.commit()
                print(cursor.rowcount, "record updated")
                cursor.close()
                db.close()  
            else:
                print(f"ERROR FAIL writing rewards to SC tx: {tx_hash.hex()}")

        print(f"{datetime.datetime.now()} sleeping 30 seconds")
        time.sleep(30)

    except Exception as e:
        print("ERROR")
        print(e)
        print(traceback.format_exc())

        print("Sleeping 30 minutes")
        time.sleep(1800)
