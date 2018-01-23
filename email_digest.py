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
from time import sleep

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
        'sender_name': 'Wudd digest',
        'recipient_address': CONFIG_MANDATORY,
        'recipient_name': '',
    }
}


config = Config(allow_no_value=True)
config.read_dict(DEFAULT_CONFIG)  # load defaults
config.read(CONFIG_FILE)
config.check_mandatory()

######################################################################

URI_BASE =   'https://a.wunderlist.com/api/v1/'
URI_USER =   'https://a.wunderlist.com/api/v1/user'
URI_LIST =   'https://a.wunderlist.com/api/v1/lists'
URI_TASK =   'https://a.wunderlist.com/api/v1/tasks'
URI_FOLDER = 'https://a.wunderlist.com/api/v1/folders'

HTTP = Http()
TODAY = date.today()


class APIUnavailableException(Exception):
    pass


def handleGET(uri):
    response, body = HTTP.request(uri, headers={
        "X-Access-Token": config.get('api', 'access_token'),
        "X-Client-ID": config.get('api', 'client_id')
    })
    if response.status != 200:
        raise APIUnavailableException("Wunderlist API unavailable, status: %s" % response.status)
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
    def __init__(self, title, dueDate, listName, starred=False):
        self.title = title.strip()
        self.dueDate = datetime.strptime(dueDate, '%Y-%m-%d').date() if dueDate else None
        self.listName = listName.lower().strip() if listName else ''
        self.starred = starred

    @property
    def titleHtml(self,):
        return formatLinks(self.title)


sectionTemplate = u'''
<div id="%(id)s">
    <h1 style="font-size: 16px; padding-left: 5px;">%(header)s</h1>
    <table width="100%%" cellpadding="5" style="border-collapse: collapse;">
%(content)s
    </table>
</div>
'''


tableRowTemplate = u'''\
        <tr style="background-color: white; border-bottom: 1px solid #cfcabc;">
            <td>%s</td>
            <td style="text-align: right; font-size: 80%%; color: #888">%s</td>
        </tr>
'''


emailTemplate = u'''
<!doctype html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-size: 11pt;">
<div id="container" style="max-width: 900px; background-color: #efeadd; padding: 5px;">
%s
<div style="text-align: right; font-size: 70%%;">Generated by <a href="https://bitbucket.org/mthu/wudd">Wudd</a></div>
</div>
</body>
</html>
'''


GROUPS = [
    {
        'id':           'today',
        'condition':    lambda task: task.dueDate is not None and (task.dueDate - TODAY).days <= 0,
        'sortKey':      lambda task: (task.dueDate, task.listName),
        'header':       lambda: u'Dnes je ' + TODAY.strftime('%A %d. %B').decode('utf-8').lower(),
        'ifEmpty':      lambda: u'Vše hotovo :-)',
        'taskText':     lambda task: task.title + (u' (starší nesplněné)' if task.dueDate < TODAY else u'') + u' (%s)' % task.listName,
        'taskHtml':     lambda task: (
                            task.titleHtml + (u' <span style="color: red;">starší nesplněné</span>' if task.dueDate < TODAY else ''),
                            task.listName
                        ),
    },
    {
        'id':           'starred',
        'condition':    lambda task: task.starred,
        'sortKey':      lambda task: (task.listName, task.title),
        'header':       lambda: u'S hvězdičkou',
        'taskHtml':     lambda task: (
                            task.titleHtml + (
                                (u' <span style="color: red;">starší nesplněné</span>'
                                    if task.dueDate < TODAY
                                    else task.dueDate.strftime(' <span style="color: green; font-size: 80%%;">%A %d. %B').decode('utf8').lower())
                                 if task.dueDate
                                 else ''),
                            task.listName
                        ),
        'taskText':     lambda task: task.title + (
                            (u' (starší nesplněné)'
                                if task.dueDate < TODAY
                                else task.dueDate.strftime(' (%A %d. %B)').decode('utf-8').lower())
                             if task.dueDate
                             else ''
                        )
     },
     {
        'id':           'inbox',
        'condition':    lambda task: task.dueDate is None and task.listName == 'inbox',
        'sortKey':      lambda task: task.title,
        'header':       lambda: u'Nezařazené v inboxu',
        'taskHtml':     lambda task: (task.titleHtml, ''),
        'taskText':     lambda task: task.title
    },
    {
        'id':           'tomorrow',
        'condition':    lambda task: task.dueDate is not None and (task.dueDate - TODAY).days == 1,
        'sortKey':      lambda task: (task.listName, task.title),
        'header':       lambda: u'Zítra',
        'taskText':     lambda task: task.title + (u' (starší nesplněné)' if task.dueDate < TODAY else u'') + u' (%s)' % task.listName,
        'taskHtml':     lambda task: (
                            task.titleHtml,
                            task.listName
                        ),
    },
    {
        'id':           'future',
        'condition':    lambda task: task.dueDate is not None and (task.dueDate - TODAY).days in range(2, 7),
        'sortKey':      lambda task: (task.dueDate, task.listName, task.title),
        'header':       lambda: u'Dalších 6 dní',
        'taskHtml':     lambda task: (
                            task.titleHtml + (task.dueDate.strftime(' <span style="color: green; font-size: 80%%;">%A %d. %B')).decode('utf8').lower(),
                            task.listName
                        ),
        'taskText':     lambda task: task.title + task.dueDate.strftime(' (%A %d. %B)').decode('utf-8').lower()
    },
]


def loadLists():
    lists = handleGET(URI_LIST)
    folders = handleGET(URI_FOLDER)

    # map list_id -> folder_name
    listFolderMapping = {listId: folder['title'] for folder in folders for listId in folder.get('list_ids', [])}

    # add title prefix for lists within a folder
    for oneList in lists:
        if oneList['id'] in listFolderMapping:
            oneList['title'] = u'%s / %s' % (listFolderMapping[oneList['id']], oneList['title'])

    return lists


def loadAllTasks():
    tasks = []

    # download list of task lists
    lists = loadLists()

    # download tasks for each list
    for oneList in lists:
        tasks.extend([
            Task(task['title'], task.get('due_date'), oneList['title'], task.get('starred', False))
            for task
            in handleGET(URI_TASK + '?list_id=' + str(oneList['id']))
        ])
    return tasks


def selectGroup(tasks, group):
    res = [task for task in tasks if group['condition'](task)]
    sortKey = group.get('sortKey')
    if sortKey:
        res.sort(key=sortKey)
    return res


def groupTasks(tasks, definitions):
    return [
        {
            'tasks': selectGroup(tasks, definition),
            'definition': definition,
        } for definition in definitions
    ]


def sendMessage(subjectNote, html, text):
    msg = MIMEMultipart('alternative')

    msg['From'] = "%s <%s>" % (Header(config.get('smtp', 'sender_name'), 'utf-8'), config.get('smtp', 'sender_address'))
    msg['To'] = "%s <%s>" % (Header(config.get('smtp', 'recipient_name'), 'utf-8'), config.get('smtp', 'recipient_address'))
    msg['Date'] = formatdate()
    msg['Subject'] = Header(u"Denní přehled" + (u' (%s)' % subjectNote if subjectNote else u''), 'utf-8')

    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8')) #the default one should be the last

    client = SMTP(config.get('smtp', 'server'))
    client.sendmail(config.get('smtp', 'sender_address'), config.get('smtp', 'recipient_address'), msg.as_string())


def getTextForGroup(group):
    definition = group['definition']
    tasks = group['tasks']
    if tasks:
        output = [definition['header']()]
        for task in tasks:
            output.append(definition['taskText'](task))
        return u'\n'.join(output)
    else:
        if definition.get('ifEmpty'):
            return definition['header']() + u':\n' + definition['ifEmpty']()
        else:
            return ''


def getText(groups):
    groups = [getTextForGroup(group) for group in groups]
    return u'\n\n'.join(group for group in groups if group)


def getHtmlForGroup(group):
    definition = group['definition']
    tasks = group['tasks']
    if tasks:
        return sectionTemplate % {
            'id': definition['id'],
            'header': definition['header'](),
            'content': u'\n'.join(tableRowTemplate % definition['taskHtml'](task) for task in tasks)
        }
    else:
        if definition.get('ifEmpty'):
            return sectionTemplate % {
                'id': group['definition']['id'],
                'header': definition['header'](),
                'content': tableRowTemplate % (definition['ifEmpty'](), ''),
            }
        else:
            return ''


def getHtml(groups):
    groups = [getHtmlForGroup(group) for group in groups]
    content = u'\n'.join(group for group in groups if group)
    return emailTemplate % content


def main(attempts=5):
    try:
        process()
    except APIUnavailableException:
        if attempts <= 1:
            raise
        else:
            sleep(120)  # wait some time and try again
            main(attempts - 1)


def process():
    groups = groupTasks(loadAllTasks(), GROUPS)
    html = getHtml(groups)
    text = getText(groups)

    def nform(choices, number):
        if number == 1:
            return choices[0]
        elif number >= 2 and number <= 4:
            return choices[1]
        else:
            return choices[2]

    # email subject
    inboxTaskCount = len([x['tasks'] for x in groups if x['definition']['id'] == 'inbox'][0])
    todayTaskCount = len([x['tasks'] for x in groups if x['definition']['id'] == 'today'][0])
    starredTaskCount = len([x['tasks'] for x in groups if x['definition']['id'] == 'starred'][0])

    inboxNote = nform((u'%d nezařazený', u'%d nezařazené', u'%d nezařazených'), inboxTaskCount) % inboxTaskCount \
                 if inboxTaskCount else ''
    todayNote = u'%d dnes' % todayTaskCount if todayTaskCount else ''
    starredNote = u'%d s hvězdičkou' % starredTaskCount if starredTaskCount else ''

    subjectNote = u' + '.join(x for x in (todayNote, starredNote, inboxNote) if x)

    # send message
    sendMessage(subjectNote, html, text)

if __name__ == '__main__':
    main()

