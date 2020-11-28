# UNITE REWARD ALLOCATION WORKER

Python worker that rewards users based on the contents of the twitter table.

* Every 15 seconds, check twitter table and write to rewards table for active campaigns
* Jupyter notebook for manually assigning rewards


## Setup 
Tested with python 3.7.7

Install libraries in requirements.txt using
`pip3 install -r requirements.txt`

## Description of relevant files

* app.py: worker