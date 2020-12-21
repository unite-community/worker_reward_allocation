import base64
import mysql.connector as mysql
import datetime
import pandas as pd
import time

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

###############################
### ROUND UP TO NEXT K MINUTES
###############################
def round_time(dt=None, date_delta=datetime.timedelta(minutes=1), to='average'):
    """
    Round a datetime object to a multiple of a timedelta
    dt : datetime.datetime object, default now.
    dateDelta : timedelta object, we round to a multiple of this, default 1 minute.
    from:  http://stackoverflow.com/questions/3463930/how-to-round-the-minute-of-a-datetime-object-python
    """
    round_to = date_delta.total_seconds()
    if dt is None:
        dt = datetime.now()
    seconds = (dt - dt.min).seconds

    if seconds % round_to == 0 and dt.microsecond == 0:
        rounding = (seconds + round_to / 2) // round_to * round_to
    else:
        if to == 'up':
            # // is a floor division, not a comment on following line (like in javascript):
            rounding = (seconds + dt.microsecond/1000000 + round_to) // round_to * round_to
        elif to == 'down':
            rounding = seconds // round_to * round_to
        else:
            rounding = (seconds + round_to / 2) // round_to * round_to

    return dt + datetime.timedelta(0, rounding - seconds, - dt.microsecond)

# next run is now
nextrun15seconds = datetime.datetime.now()

while True:
    now = datetime.datetime.now()

    if now > nextrun15seconds:
        print(f"XXX, {now}, {nextrun15seconds}")

        try:

            ##########################
            ### WRITE TO REWARDS TABLE
            ##########################
            print(now, 'do 15s stuff')


            ##################
            ## FETCH BLACKLIST
            ##################

            db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
            cursor = db.cursor()
            query = f'SELECT twitter_username FROM blacklist;'
            cursor.execute(query)
            records = cursor.fetchall()
            cursor.close()
            db.close()
            blacklist_db = []
            for record in records:
                blacklist_db.append(record[0])


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

            #################################################
            ### WRITE TO REWARDS TABLE BASED ON TWITTER TABLE
            #################################################

            for campaign in campaigns:
                print(f"Begin rewards process for campaign {campaign_id}")


                ################
                ## GET WHITELIST
                ################

                db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
                cursor = db.cursor()
                query = f'SELECT twitter_username FROM whitelist WHERE campaign_id = %s AND manager_ethereum_address = %s;'
                values = (campaign['campaign_id'], campaign['manager_ethereum_address'])
                cursor.execute(query, values)
                records = cursor.fetchall()
                cursor.close()
                db.close()
                whitelist = []
                for record in records:
                    whitelist.append(record[0])

                has_whitelist = False
                if len(whitelist) > 0:
                    has_whitelist = True
                    print(f"campaign has whitelist with {len(whitelist)} whitelisted users (id: {campaign['campaign_id']}, manager_ethereum_address: {campaign['manager_ethereum_address']})")


                ###########################
                ### GET TWEETS FOR CAMPAIGN
                ###########################

                # get tweets for this campaign
                db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
                cursor = db.cursor()
                query = f'SELECT id, tweet_id, referenced_tweet_id, twitter_handle, author_id, created_at, following, following_processed FROM twitter where referenced_tweet_id = "{campaign["twitter_status_id"]}";'
                cursor.execute(query)
                records_tweets = cursor.fetchall()
                cursor.close()
                db.close()
                #
                tweets = []
                for record in records_tweets:
                    tweets.append(dict(zip(['id', 'tweet_id', 'referenced_tweet_id', 'twitter_handle', 'author_id', 'created_at', 'following', 'following_processed'], record)))
                tweets = pd.DataFrame(tweets)

                # subset to only users who are following if campaign is rtf
                if campaign['campaign_type'] == 'rtf': 
                    print("Campaign is RTF - subsetting out non-following users")
                    tweets = tweets[tweets['following'] == 1]
                tweet_handles = list(tweets['twitter_handle'].unique())
                tweet_handles = [h.lower() for h in tweet_handles]


                ##########################
                ### WORK OUT WHO TO REWARD
                ##########################

                # get all rewards for this campaign as dataframe
                db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
                cursor = db.cursor()
                manager_ethereum_address = campaign['manager_ethereum_address']
                campaign_id = campaign['campaign_id']
                query = f'SELECT id, campaign_id, twitter_handle, blockchain_write_time FROM rewards where campaign_id = "{campaign_id}" and manager_ethereum_address = "{manager_ethereum_address}";'
                cursor.execute(query)
                records_rewards = cursor.fetchall()
                cursor.close()
                db.close()
                # convert to dataframe
                rewards = []
                for record in records_rewards:
                    rewards.append(dict(zip(['id', 'campaign_id', 'twitter_handle', 'blockchain_write_time'], record)))
                df = pd.DataFrame(rewards)

                # get list of handles already rewarded for this campaign
                handles = []
                if len(df) > 0:
                    handles = list(df['twitter_handle'].unique())
                    handles = [h.lower() for h in handles]

                # going to decrement rewards remaining as they're assigned
                rewards_remaining = campaign['rewards_remaining']

                # loop over handles and check who to reward
                for i, handle in enumerate(tweet_handles):
                    print(f"{i+1} / {len(tweets)} checking if {handle} already rewarded")

                    # user must be "registered" and not blacklisted
                    if handle not in blacklist_db:
                        if handle in user_list:
                            # campaign must have rewards left
                            if rewards_remaining > 0:
                                if 'twitter_handle' in list(df.columns) and handle in handles:
                                    print(f"  # {handle} already rewarded")
                                else:
                                    
                                    # check whitelist
                                    reward_user = True
                                    if has_whitelist and handle not in whitelist:
                                        reward_user = False
                                        print(f"  # {handle} not on whitelist")
                                    
                                    if reward_user:
                                        print(f"  # {handle} needs rewards")
                                        # write rewards to database (with null blockchainwritetime)
                                        db = mysql.connect(host=secret['DBHOST'],user=secret['DBUSER'],passwd=secret['DBPASS'],database=secret['DBTABLE'])
                                        cursor = db.cursor()
                                        query = "INSERT INTO rewards (campaign_id, twitter_handle, manager_ethereum_address) VALUES (%s, %s, %s);"
                                        values = (campaign['campaign_id'], handle, campaign['manager_ethereum_address'])
                                        cursor.execute(query, values)
                                        db.commit()
                                        print(cursor.rowcount, "record inserted")
                                        cursor.close()
                                        db.close()

                                        rewards_remaining -= 1
                            else:
                                print("Rewards exceeded for campaign")
                                break
                        else:
                            print(f"  # User {handle} not registered")
                    else:
                        print(f"  # User {handle} blacklisted")


            # update next run to 15 seconds from now
            nextrun15seconds = now + datetime.timedelta(seconds=15)

            print(f'{now} NEXT 15sec RUN: {nextrun15seconds}')

        except Exception as e:
            print("ERROR")
            print(e)
    else:
        print(f"{now} sleeping 15 seconds")
        time.sleep(15)
