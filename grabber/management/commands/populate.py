from collections import namedtuple
from io import BytesIO
from urllib.request import urljoin
import csv
import random
import string
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand

from bs4 import BeautifulSoup
import arrow
import pycurl
import requests

from ...models import Issue, Version, Attachment

User = namedtuple('User', ['name', 'displayName', 'emailAddress', 'active'])


class Command(BaseCommand):
    help = 'Populate JIRA'

    HOST = settings.JIRA['DESTINATION']['HOST']
    PKEY = settings.JIRA['DESTINATION']['PROJECT']['KEY']
    PID = settings.JIRA['DESTINATION']['PROJECT']['ID']
    AUTH = settings.JIRA['DESTINATION']['AUTH']
    DEFAULT_USER_PASSWORD = settings.JIRA['DESTINATION']['DEFAULT_USER_PASSWORD']

    SUBTASK_IDS = settings.SUBTASK_IDS
    STORY_ID = settings.STORY_ID
    EPIC_ID = settings.EPIC_ID

    ISSUE_TYPES = settings.ISSUE_TYPES
    STATUSES = settings.STATUSES
    LINK_TYPES = settings.LINK_TYPES

    def handle(self, *args, **options):
        self.create_users()
        self.create_issues()
        self.create_versions()
        self.set_issues_versions()
        self.make_relations()
        self.do_transitions()
        self.make_links()
        self.create_comments()
        self.create_attachments()
        self.set_random_users_passwords()
        self.deactivate_users()

        self.generate_issue_creation_dates_csv()

    def set_issues_versions(self):
        print('Setting Issues Versions')
        for issue in Issue.objects.all():
            versions = issue.json['fields']['fixVersions']
            if versions:
                url = urljoin(self.HOST, '/rest/api/2/issue/{key}'.format(key=issue.key))
                names = [{'name': d['name']} for d in versions]
                print(issue, names)
                payload = {
                    "update": {
                        "fixVersions": [
                            {"set": names}
                        ]
                    }
                }
                r = requests.put(url=url, json=payload, auth=self.AUTH)
                r.raise_for_status()

    def create_versions(self):
        print('Creating Versions')
        url = urljoin(self.HOST, '/rest/api/2/version')

        for version in Version.objects.all():
            print(version)
            data = version.json
            payload = {
                "name": data['name'],
                "archived": data['archived'],
                "released": data['released'],
                "project": self.PKEY,
                "projectId": self.PID
            }
            description = data.get('description')
            if description:
                payload['description'] = description
            userStartDate = data.get('userStartDate')
            if userStartDate:
                payload['userStartDate'] = userStartDate
            userReleaseDate = data.get('userReleaseDate')
            if userReleaseDate:
                payload['userReleaseDate'] = userReleaseDate

            r = requests.post(url=url, json=payload, auth=self.AUTH)
            r.raise_for_status()

    def create_issues(self):
        print('Creating Issues')
        issues_keys = list(Issue.objects.values_list('key', flat=True))

        last_id = int(issues_keys[-1].split('-')[1])
        for _id in range(1, last_id + 1):
            key = '{}-{}'.format(self.PKEY, _id)
            print(key)
            try:
                issue = Issue.objects.get(key=key)
            except Issue.DoesNotExist as err:
                print(err)
                data = None
            else:
                data = issue.json

            self._create_basic_issue(data=data)

    def make_relations(self):
        print('Making Relations')
        for issue in Issue.objects.filter(json__fields__issuetype__id__has_any_keys=self.SUBTASK_IDS):
            print(issue.key)
            self._make_subtask_relation(issue=issue)

    def do_transitions(self):
        print('Doing Transitions')
        for issue in Issue.objects.all():
            print(issue)
            self._do_transition(issue=issue)

    def make_links(self):
        print('Making Links')
        for issue in Issue.objects.all():
            print(issue)
            for link in issue.json['fields']['issuelinks']:
                print(link)
                self._make_link(key=issue.key, link=link)

    def create_comments(self):
        print('Creating Comments')
        for issue in Issue.objects.all():
            print(issue)
            for comment in issue.json['fields']['comment']['comments']:
                self._create_comment(key=issue.key, body=comment['body'],
                                     auth=(comment['author']['name'], self.DEFAULT_USER_PASSWORD))

    def create_attachments(self):
        print('Creating Attachments')
        for attachment in Attachment.objects.all().select_related('issue'):
            print(attachment.issue.key, attachment)
            self._create_attachment(attachment=attachment)

    @staticmethod
    def generate_issue_creation_dates_csv():
        """
        https://confluence.atlassian.com/jirakb/how-to-change-the-issue-creation-date-using-csv-import-779160699.html
        :return:
        """
        print('Generating CSV')
        with open('issue_creation_date.csv', 'w') as f:
            writer = csv.writer(f)
            for issue in Issue.objects.all():
                # Use this format in JIRA: yyyy-MM-dd HH:mm:ss
                created = arrow.get(issue.json['fields']['created']).format('YYYY-MM-DD HH:mm:ss')
                summary = issue.json['fields']['summary'] or 'Empty'
                writer.writerow((issue.key, created, summary))

    def _create_comment(self, *, key, body, auth):
        url = urljoin(self.HOST, '/rest/api/2/issue/{key}/comment'.format(key=key))

        r = requests.post(url, json={'body': body}, auth=auth)
        r.raise_for_status()

    def _create_basic_issue(self, *, data=None):
        payload = {
            "fields": {
                "project": {
                    "id": self.PID
                },
                "summary": data['fields']['summary'] if data else 'Empty',
                "issuetype": {"id": self.STORY_ID},
                "assignee": {
                    "name": data['fields']['assignee']['name'] if (data and data['fields']['assignee']) else None,
                },
                "reporter": {
                    "name": data['fields']['reporter']['name'] if data else self.AUTH[0],
                },
                "priority": {
                    "id": data['fields']['priority']['id'] if data else '5'  # minor by default
                },
                "labels": data['fields']['labels'] if data else [],
                "description": (data['fields']['description'] or '') if data else 'Empty',
            }
        }

        # {"errorMessages":[],"errors":{"customfield_10004":"Epic Name is required."}}
        if data:
            issue_type = self.ISSUE_TYPES.get(data['fields']['issuetype']['id'])
            if issue_type == self.EPIC_ID:
                payload['fields']['issuetype']['id'] = issue_type
                payload['fields']['customfield_10004'] = data['fields']['customfield_10004'] or 'Empty'

        r = requests.post(url=urljoin(self.HOST, '/rest/api/2/issue'), json=payload, auth=self.AUTH)
        print(r.text)
        r.raise_for_status()

        r_data = r.json()
        if data:
            if data['key'] != r_data['key']:
                raise ValueError('Ключи задач не совпадают!')
            issue = Issue.objects.get(key=data['key'])
            issue.uid_dest = r_data['id']
            issue.link_dest = r_data['self']
            issue.save()

    def _make_subtask_relation(self, *, issue):
        uid_dest = str(issue.uid_dest)
        session = requests.Session()

        url0 = urljoin(self.HOST, '/secure/ConvertIssueSetIssueType.jspa?id=' + uid_dest)
        r = session.get(url=url0, auth=self.AUTH)
        soup = BeautifulSoup(r.text)
        guid = soup.find_all("input", type="hidden", id="guid")[0]['value']

        # Step 1: Select Parent and Sub-task Type
        url_s1 = urljoin(self.HOST, '/secure/ConvertIssueSetIssueType.jspa')
        payload_s1 = {
            "parentIssueKey": issue.json['fields']['parent']['key'],
            "issuetype": "10000",
            "id": uid_dest,
            "guid": guid,
            "Next >>": "Next >>",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }

        r = session.post(url=url_s1, data=payload_s1, headers={"Referer": url0})
        r.raise_for_status()

        # Step 2: Update Fields
        url_s2 = urljoin(self.HOST, '/secure/ConvertIssueUpdateFields.jspa')
        payload_s2 = {
            "id": uid_dest,
            "guid": guid,
            "Next >>": "Next >>",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }

        r = session.post(url=url_s2, data=payload_s2)
        r.raise_for_status()

        # Step 3: Confirm the conversion with all of the details you have just configured
        url_s3 = urljoin(self.HOST, '/secure/ConvertIssueConvert.jspa')
        payload_s3 = {
            "id": uid_dest,
            "guid": guid,
            "Finish": "Finish",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }

        r = session.post(url=url_s3, data=payload_s3)
        r.raise_for_status()

    @staticmethod
    def _get_users():
        issues = Issue.objects.values_list('json', flat=True)
        users = set()

        for issue in issues:
            assignee = issue['fields']['assignee']
            if assignee:

                users.add(User(
                    name=assignee['name'],
                    displayName=assignee['displayName'],
                    emailAddress=assignee['emailAddress'],
                    active=assignee['active']))

            reporter = issue['fields']['reporter']
            if reporter:
                users.add(User(
                    name=reporter['name'],
                    displayName=reporter['displayName'],
                    emailAddress=reporter['emailAddress'],
                    active=reporter['active']))

            for comment in issue['fields']['comment']['comments']:
                comment_author = comment['author']
                users.add(User(
                    name=comment_author['name'],
                    displayName=comment_author['displayName'],
                    emailAddress=comment_author['emailAddress'],
                    active=comment_author['active']))

        return users

    def create_users(self):
        print('Creating Users')
        users = self._get_users()

        for user in users:
            print(user)
            self._create_user(user=user)

    def set_random_users_passwords(self):
        print('Setting Random Users Passwords')
        users = self._get_users()
        for user in users:
            if user.name in [self.AUTH[0]]:
                continue
            password = self._get_random_string()
            print(user, password)
            self._set_password(user=user, password=password)

    def deactivate_users(self):
        print('Deactivating Users')
        users = self._get_users()
        for user in users:
            if not user.active:
                print(user)
                self._deactivate_user(user=user)

    def _deactivate_user(self, *, user):
        session = self._get_sudo_session()

        url = urljoin(self.HOST, '/secure/admin/user/EditUser.jspa')
        payload = {
            "inline": "true",
            "decorator": "dialog",
            "username": user.name,
            "fullName": user.displayName,
            "email": user.emailAddress,
            "editName": user.name,
            "returnUrl": "UserBrowser.jspa",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }
        r = session.post(url=url, data=payload)
        r.raise_for_status()

    @staticmethod
    def _get_random_string(size=20):
        return ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(size))

    def _set_password(self, *, user, password):
        session = self._get_sudo_session()

        url = urljoin(self.HOST, '/secure/admin/user/SetPassword.jspa')
        payload = {
            "inline": "true",
            "decorator": "dialog",
            "password": password,
            "confirm": password,
            "name": user.name,
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }
        r = session.post(url=url, data=payload)
        r.raise_for_status()

    def _get_sudo_session(self):
        user, password = self.AUTH
        session = requests.Session()

        # Get initial cookies
        url = urljoin(self.HOST, '/secure/admin/user/AddUser!default.jspa')
        r = session.get(url=url, auth=self.AUTH)
        r.raise_for_status()

        # Login form
        url_login = urljoin(self.HOST, '/login.jsp')
        payload_login = {
            "os_username": user,
            "os_password": password,
            "os_destination": "/secure/admin/user/AddUser!default.jspa",
            "user_role": "ADMIN",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
            "login": "Log In",
        }
        r = session.post(url=url_login, data=payload_login)
        r.raise_for_status()

        # Sudo login form
        url_login_sudo = urljoin(self.HOST, '/secure/admin/WebSudoAuthenticate.jspa')
        payload_login_sudo = {
            "webSudoPassword": password,
            "webSudoDestination": "/secure/admin/user/AddUser!default.jspa",
            "webSudoIsPost": "false",
            "atl_token": session.cookies.get('atlassian.xsrf.token'),
        }
        r = session.post(url=url_login_sudo, data=payload_login_sudo)
        r.raise_for_status()

        return session

    def _create_user(self, *, user):
        session = self._get_sudo_session()

        # Create user
        url_user = urljoin(self.HOST, '/secure/admin/user/AddUser.jspa')
        payload_user = {
            'email': user.emailAddress,
            'fullname': user.displayName,
            'username': user.name,
            'password': self.DEFAULT_USER_PASSWORD,
            'selectedApplications': 'jira-software',
            'Create': 'Create user',
            'atl_token': session.cookies.get('atlassian.xsrf.token'),
        }
        r = session.post(url=url_user, data=payload_user)
        r.raise_for_status()

    def _do_transition(self, *, issue):
        url = urljoin(self.HOST, '/rest/api/2/issue/{key}/transitions'.format(key=issue.key))

        status = issue.json['fields']['status']['name']
        payload = {
            'transition': {'id': self.STATUSES[status]},
        }

        r = requests.post(url=url, json=payload, auth=self.AUTH)
        r.raise_for_status()

    def _make_link(self, *, key, link):
        url = urljoin(self.HOST, '/rest/api/2/issueLink')

        link_type = self.LINK_TYPES.get(link['type']['name'])
        if 'outwardIssue' in link:
            inwardIssue = key
            outwardIssue = link['outwardIssue']['key']
        elif 'inwardIssue' in link:
            inwardIssue = link['inwardIssue']['key']
            outwardIssue = key

        payload = {
            "type": {
                "name": link_type
            },
            "inwardIssue": {
                "key": inwardIssue
            },
            "outwardIssue": {
                "key": outwardIssue
            },
        }

        r = requests.post(url=url, json=payload, auth=self.AUTH)
        r.raise_for_status()

    def _create_attachment(self, *, attachment):
        url = urljoin(self.HOST, '/rest/api/2/issue/{key}/attachments'.format(key=attachment.issue.key))

        file_path = attachment.attachment.path

        # fixme
        # *** requests ***
        # files = {'file': open(file_path, 'rb')}
        #
        # r = requests.post(url=url, files=files, auth=(attachment.json['author']['name'], self.DEFAULT_USER_PASSWORD),
        #                   headers={"X-Atlassian-Token": "no-check"})
        # print(r.status_code)
        # print(r.text)
        # r.raise_for_status()

        # *** pycurl ***
        # buffer = BytesIO()
        # c = pycurl.Curl()
        # c.setopt(c.WRITEDATA, buffer)
        # c.setopt(c.URL, url)
        # c.setopt(pycurl.USERPWD, '%s:%s' % (attachment.json['author']['name'], self.DEFAULT_USER_PASSWORD))
        # c.setopt(pycurl.HTTPHEADER, ("X-Atlassian-Token", "no-check"))
        #
        # c.setopt(c.HTTPPOST, [
        #     ('file', (
        #         # upload the contents of this file
        #         c.FORM_FILE, file_path.encode('utf-8'),
        #     )),
        # ])
        #
        # c.perform()
        # c.close()
        #
        # body = buffer.getvalue()
        # print(body.decode('utf-8'))

        # *** curl workaround ***
        # curl --verbose -D- -u {name}:{password} -X POST -H "X-Atlassian-Token: no-check" -F "file=@{file_path}" {url}
        args = ['curl', '--verbose', '-D-', '-u',
                '{}:{}'.format(attachment.json['author']['name'], self.DEFAULT_USER_PASSWORD),
                '-X', 'POST', '-H', 'X-Atlassian-Token: no-check', '-F',
                'file=@{}'.format(file_path), url
                ]

        output = subprocess.check_output(args)
