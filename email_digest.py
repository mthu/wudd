#!/usr/bin/env python
# coding: utf-8
#
# Wudd - sends daily digest of user's tasks stored on Wunderlist, using their official API
#
# Copyright (c) 2016, Martin Plicka <https://mplicka.cz>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation and/or
# other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY
# OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.
#

import simplejson
import locale
import re
import os

from httplib2 import Http

from datetime import date, datetime

from collections import defaultdict
from ConfigParser import RawConfigParser

from email import charset
from email.header import Header
from email.utils import formatdate
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from smtplib import SMTP

# Send unicode texts in emails with QP instead of Base64 encoding
charset.add_charset('utf-8', charset.QP, charset.QP)

# Locale for day names in dates
locale.setlocale(locale.LC_TIME, 'cs_CZ.UTF-8')


######################################################################
# Config #

CONFIG_MANDATORY = object()

class Config(RawConfigParser):

    def read_dict(self, structured_config):
        if structured_config:
            for section, items in structured_config.iteritems():
                if not self.has_section(section):
                    self.add_section(section)
                for item, value in items.iteritems():
                    self.set(section, item, value)

    def check_mandatory(self):
        missing = defaultdict(list)
        for section, values in self._sections.iteritems():
            for name, value in values.iteritems():
                if value is CONFIG_MANDATORY:
                    missing[section].append(name)
        if missing:
            raise Exception("Missing config", dict(missing))


# hardcoded config file
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.ini')

DEFAULT_CONFIG = {
    'api': {
        'client_id': CONFIG_MANDATORY,
        'access_token': CONFIG_MANDATORY,
    },
    'smtp': {
        'server': '127.0.0.1',
        'sender_address': CONFIG_MANDATORY,
        'sender_name': 'Wunderlist email digest',
        'recipient_address': CONFIG_MANDATORY,
        'recipient_name': '',
    }
}


config = Config(allow_no_value=True)
config.read_dict(DEFAULT_CONFIG)  # load defaults
config.read(CONFIG_FILE)
config.check_mandatory()

######################################################################

URI_BASE = 'https://a.wunderlist.com/api/v1/'
URI_USER = 'https://a.wunderlist.com/api/v1/user'
URI_LIST = 'https://a.wunderlist.com/api/v1/lists'
URI_TASK = 'https://a.wunderlist.com/api/v1/tasks'

HTTP = Http()
TODAY = date.today()


def handleGET(uri):
    response, body = HTTP.request(uri, headers={
        "X-Access-Token": config.get('api', 'access_token'),
        "X-Client-ID": config.get('api', 'client_id')
    })
    return simplejson.loads(body)


def formatLinks(message):
    message = re.sub(
        r'(?P<begin>(^|[ ]))(?P<link>(http|https|ftp)://[^/ \n]*(/[^/ \n]*)*[^., \n])(?P<end>[,.]*($|[ ]))',
        r'\g<begin><a href="\g<link>">\g<link></a>\g<end>',
        message,
        flags=re.M | re.I
    )
    message = re.sub(r'\n', r'<br>', message, flags=re.M)
    return message


class Task(object):
    def __init__(self, title, dueDate, listName):
        self.title = title
        self.dueDate = datetime.strptime(dueDate, '%Y-%m-%d').date() if dueDate else None
        self.listName = listName.lower() if listName else ''

    @property
    def titleHtml(self,):
        return formatLinks(self.title)


GROUPS = {
    'inbox': {
        'condition':    lambda task: task.dueDate is None and task.listName == 'inbox',
    },
    'today': {
        'condition':    lambda task: task.dueDate is not None and (task.dueDate - TODAY).days <= 0,
        'sortKey':      lambda task: task.dueDate,
    },
    'future': {
        'condition':    lambda task: task.dueDate is not None and (task.dueDate - TODAY).days in range(1, 7),
        'sortKey':      lambda task: task.dueDate,
    }
}


def loadAllTasks():
    tasks = []

    # download list of task lists
    lists = handleGET(URI_LIST)    
    
    # download tasks for each list
    for oneList in lists:
        tasks.extend([
            Task(task['title'], task.get('due_date'), oneList['title'])
            for task
            in handleGET(URI_TASK + '?list_id=' + str(oneList['id']))
        ])
    return tasks


def groupTasks(tasks, rules):
    groups = {key: [] for key in rules.iterkeys()}

    for task in tasks:
        for key, definition in rules.iteritems():
            if definition['condition'](task):
                groups[key].append(task)

    for key, tasks in groups.iteritems():
        sortKey = rules[key].get('sortKey')
        if sortKey:
            tasks.sort(key=sortKey)
    return dict(groups)


def sendMessage(subjectNote, html, text):
    msg = MIMEMultipart('alternative')

    msg['From'] = "%s <%s>" % (Header(config.get('smtp', 'sender_name'), 'utf-8'), config.get('smtp', 'sender_address'))
    msg['To'] = "%s <%s>" % (Header(config.get('smtp', 'recipient_name'), 'utf-8'), config.get('smtp', 'recipient_address'))
    msg['Date'] = formatdate()
    msg['Subject'] = Header(u"Denní přehled" + (u' ' + subjectNote if subjectNote else u''), 'utf-8')

    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8')) #the default one should be the last

    client = SMTP(config.get('smtp', 'server'))
    client.sendmail(config.get('smtp', 'sender_address'), config.get('smtp', 'recipient_address'), msg.as_string())

def main():
    groups = groupTasks(loadAllTasks(), GROUPS)

    sectionTemplate = u'''
        <h1 style="font-size: 16px; padding-left: 5px;">%(caption)s</h1>
        <div id="%(id)s">
            %(content)s
        </div>
    '''

    tableTemplate = u'''
        <table width="100%%" cellpadding="5" style="border-collapse: collapse;">
            %(content)s
        </table>    
    '''

    tableRowTemplate = u'''
        <tr style="background-color: white; border-bottom: 1px solid #cfcabc;">
            <td>%(text)s</td>
            <td style="text-align: right; font-size: 80%%; color: #888">%(folder)s</td>
        </tr>
    '''

    todayText = u'Dnes je ' + TODAY.strftime('%A %d. %B').decode('utf-8').lower() + ":\n"
    if groups['today']:
        todayTable = tableTemplate % {
            'content': ''.join(
                tableRowTemplate % {
                    'text': task.titleHtml + (u' <span style="color: red;">starší nesplněné</span>' if task.dueDate < TODAY else ''),
                    'folder': task.listName
                } for task in groups['today']
            )
        }
        for task in groups['today']:
            todayText += task.title + (' (overdue)' if task.dueDate < TODAY else '') + ' (%s)'%task.listName + '\n'
    else:
        todayTable = u'<div style="background-color: white; padding: 5px; color: green;">vše hotovo :-)</div>'
        todayText += u'vše hotovo :-)\n'
    todayText += '\n'

    today = sectionTemplate % {
        'caption': u'Dnes je ' + TODAY.strftime('%A %d. %B').decode('utf-8').lower(),
        'id': 'today',
        'content': todayTable
    }

    if groups['inbox']:
        inboxTable = tableTemplate % {
            'content': u''.join(
                tableRowTemplate % {
                    'text': task.titleHtml,
                    'folder': '',
                }
                for task
                in groups['inbox']
            )
        } 
        inbox = sectionTemplate % {
            'caption': u'Nezařazené v Inboxu',
            'id': '',
            'content': inboxTable,
        }
        inboxText = u'Nezařazené v Inboxu:\n'
        for task in groups['inbox']:
            inboxText += task.title + '\n'
        inboxText += '\n'
    else:
        inbox = ''
        inboxText = ''

    if groups['future']:
        futureTable = tableTemplate % {
            'content': u''.join(
                tableRowTemplate % {
                    'text': task.titleHtml +\
                             (task.dueDate.strftime(' <span style="color: green; font-size: 80%%;">%A %d. %B')).decode('utf8').lower(),
                    'folder': task.listName
                }
                for task
                in groups['future']
            )
        } 
        future= sectionTemplate % {
            'caption': u'Další dny',
            'id': 'future',
            'content': futureTable,
        }
        futureText = u'Další dny:\n'
        for task in groups['future']:
            futureText += task.title + task.dueDate.strftime(' (%A %d. %B)').decode('utf-8').lower() + '\n'
        futureText += '\n'
    else:
        future = ''
        futureText = ''


    html = u'''
    <!doctype html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body style="font-size: 11pt;">
        <div id="container" style="max-width: 900px; background-color: #efeadd; padding: 5px;">
            %(today)s
            %(inbox)s
            %(future)s
        </div>
    </body>
    </html>
    ''' % {
        'today': today,
        'inbox': inbox,
        'future': future,
    }

    def nform(choices, number):
        if number == 1:
            return choices[0]
        elif number >= 2 and number <=4:
            return choices[1]
        else:
            return choices[2]

    inboxNote = nform((u'%d nezařazený', u'%d nezařazené', u'%d nezařazených'), len(groups['inbox'])) % len(groups['inbox']) \
                 if groups['inbox'] else ''
    if groups['today']:
        note = u'%d dnes' % len(groups['today'])
        if inboxNote:
            note += ' + ' + inboxNote
    else:
        if inboxNote:
            note = inboxNote
        else:
            note = ''

    text = todayText + inboxText + futureText

    #print html
    #print text
    sendMessage('('+ note +')' if note else '', html, text)

if __name__ == '__main__':
    main()

