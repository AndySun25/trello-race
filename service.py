# -*- coding: utf-8 -*-
import os
from datetime import datetime

import boto3
import json
import requests
from dynamodb_json import json_util as d_json
from trello import TrelloClient

from trello_config import lists

api_key = os.environ['TRELLO_API_KEY']
api_token = os.environ['TRELLO_API_TOKEN']
dynamodb_table_name = os.environ['DYNAMODB_TABLE_NAME']
slack_webhook_url = os.environ['SLACK_WEBHOOK_URL']

dynamodb_client = boto3.client('dynamodb')


messages = (
    ('did_count', {
        'title': "Cards finished",
        'none': "Unfortunately, nobody finished any cards today...",
        'single': "Congratulations to {name} for finishing {count} card(s)!",
        "multiple": "Congratulations to {name} for finishing {count} card(s) each!",
    }),
    ('new_count', {
        'title': "Cards received",
        'none': "Nobody got any new cards today, wtf?",
        'single': "Congratulations to {name} for being busiest with {count} new card(s)!",
        "multiple": "Congratulations to {name} for being busiest with {count} new card(s) each!",
    }),
    ('net_count', {
        'title': "Net result",
        'none': "Workload is steady, everybody finished with same number of cards as they started!",
        'single': "Congratulations to {name} for ending the day with {count} fewer card(s)!",
        "multiple": "Congratulations to {name} for ending the day with {count} fewer card(s) each!",
    }),
)


def get_trello_client():
    return TrelloClient(api_key, token=api_token)


def get_db_key():
    return datetime.now().date().isoformat()


def start_of_day(*args, **kwargs):
    start_of_day_data = {}
    client = get_trello_client()
    for list_id in lists:
        trello_list = client.get_list(list_id)
        card_ids = [str(card.id) for card in trello_list.list_cards()]
        start_of_day_data[list_id] = card_ids

    dynamodb_client.put_item(
        TableName=dynamodb_table_name,
        Item=d_json.dumps({
            'date': get_db_key(),
            'start_of_day': start_of_day_data,
        }, as_dict=True)
    )


def end_of_day(*args, **kwargs):
    db_key = get_db_key()
    start_of_day_entry = dynamodb_client.get_item(
        TableName=dynamodb_table_name,
        Key=d_json.dumps({'date': db_key}, as_dict=True),
    )

    try:
        start_of_day_data = d_json.loads(start_of_day_entry['Item'], as_dict=True)['start_of_day']
    except KeyError:
        print("Shit ain't here, or it's wrong")
        print(start_of_day_entry)
        return

    end_of_day_data = {}
    stats = {}

    client = get_trello_client()

    for list_id, initial_card_ids in start_of_day_data.items():
        trello_list = client.get_list(list_id)

        end_card_ids = [str(card.id) for card in trello_list.list_cards()]
        end_of_day_data[list_id] = end_card_ids

        initial_card_id_set = set(initial_card_ids)
        end_card_id_set = set(end_card_ids)

        stats[list_id] = {
            'display_name': trello_list.name,
            'did_count': len(initial_card_id_set.difference(end_card_id_set)),
            'new_count': len(end_card_id_set.difference(initial_card_id_set)),
            'net_count': len(initial_card_id_set) - len(end_card_ids),
        }

    dynamodb_client.put_item(
        TableName=dynamodb_table_name,
        Item=d_json.dumps({
            'date': get_db_key(),
            'start_of_day': start_of_day_data,
            'end_of_day': end_of_day_data,
            'stats': stats,
        }, as_dict=True)
    )

    attachments = []

    for stat_type, message_config in messages:
        stat_count = max(stats.values(), key=lambda x: x[stat_type])[stat_type]
        stat_peeps = [i['display_name'] for i in stats.values() if i[stat_type] == stat_count] if stat_count else []

        if stat_count < 0 or not stat_peeps:
            text_template = message_config['none']
            name = None
            color = 'warning'
        elif len(stat_peeps) > 1:
            text_template = message_config['multiple']
            name = ", ".join(stat_peeps)
            color = 'good'
        else:
            text_template = message_config['single']
            name = stat_peeps[0]
            color = 'good'

        attachment = {
            "fallback": "",
            "title": message_config['title'],
            "text": text_template.format(name=name, count=stat_count),
            "color": color,
        }
        attachments.append(attachment)

    day = datetime.strptime(db_key, '%Y-%m-%d')
    payload = {
        "text": "*Trello race results for {}*".format(day.strftime('%b %-d, %Y')),
        "attachments": attachments,
    }

    requests.post(slack_webhook_url, data=json.dumps(payload), headers={"Content-Type": "application/json"})
