from urllib.request import urljoin

from django.conf import settings
from django.core.management.base import BaseCommand

import requests

from ...models import Issue, Version, Attachment


class Command(BaseCommand):
    help = 'Grab JIRA'

    HOST = settings.JIRA['SOURCE']['HOST']
    PKEY = settings.JIRA['SOURCE']['PROJECT_KEY']
    AUTH = settings.JIRA['SOURCE']['AUTH']

    def handle(self, *args, **options):
        self._get_versions()
        self._get_issues_list()
        self._get_issues_details()
        self._download_attachments()

    def _get_versions(self):
        versions_endpoint = '/rest/api/2/project/%s/versions' % self.PKEY
        response = requests.get(url=urljoin(self.HOST, versions_endpoint), auth=self.AUTH).json()

        bulk_versions = [Version(name=v['name'], uid=v['id'], link=v['self'], json=v) for v in response]
        Version.objects.all().delete()
        Version.objects.bulk_create(bulk_versions)
        print('Версии загружены')

    def _get_issues_list(self):
        issues_endpoint = '/rest/api/2/search?jql=project=%s&fields=id,key&maxResults=1000&startAt={start}' % self.PKEY

        issues = []
        for start in [0, 1000, 2000, 3000, 4000]:
            response = requests.get(
                url=urljoin(self.HOST, issues_endpoint.format(start=start)),
                auth=self.AUTH,
            ).json()
            issues.extend(response['issues'])

        print('Загружено {} задач'.format(len(issues)))

        bulk_issues = [Issue(uid=i['id'], key=i['key'], link=i['self']) for i in issues]
        Issue.objects.all().delete()
        Issue.objects.bulk_create(bulk_issues)

    def _get_issues_details(self):
        for issue in Issue.objects.all():
            print('Обрабатывается:', issue)
            response = requests.get(
                url=urljoin(self.HOST, Issue.API.format(uid=issue.uid)),
                auth=self.AUTH,
            ).json()
            issue.json = response
            issue.save()

    def _download_attachments(self):
        Attachment.objects.all().delete()

        for issue in Issue.objects.all():
            for attachment in issue.json['fields']['attachment']:
                att = Attachment.objects.create(uid=attachment['id'], filename=attachment['filename'],
                                                json=attachment, issue=issue)
                att.save_file_from_url(url=attachment['content'], auth=self.AUTH)
                print(issue, att)
