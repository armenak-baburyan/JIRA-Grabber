from django.db import models
from django.contrib.postgres.fields import JSONField
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile

import requests


class Issue(models.Model):
    API = '/rest/api/2/issue/{uid}'

    uid = models.PositiveIntegerField(unique=True)
    key = models.CharField(max_length=10, unique=True)
    link = models.URLField()

    uid_dest = models.PositiveIntegerField(unique=True, null=True)
    link_dest = models.URLField(blank=True)

    json = JSONField(null=True)

    class Meta:
        verbose_name = 'issue'
        verbose_name_plural = 'issues'
        ordering = ('-id',)

    def __str__(self):
        return self.key


def generate_filename(instance, filename):
    return 'attachments/{uid}/{filename}'.format(uid=instance.issue.key, filename=filename)


class Attachment(models.Model):
    API = '/rest/api/2/attachment/{uid}'

    uid = models.PositiveIntegerField(unique=True)
    filename = models.CharField(max_length=255)
    attachment = models.FileField(upload_to=generate_filename, max_length=512)
    json = JSONField(null=True)
    issue = models.ForeignKey('Issue')

    class Meta:
        verbose_name = 'attachment'
        verbose_name_plural = 'attachments'
        ordering = ('issue',)

    def __str__(self):
        return self.filename

    def save_file_from_url(self, *, url, auth):
        r = requests.get(url=url, auth=auth)

        temp = NamedTemporaryFile(delete=True)
        temp.write(r.content)
        temp.flush()

        self.attachment.save(self.filename, File(temp), save=True)


class Version(models.Model):
    name = models.CharField(max_length=50)
    uid = models.CharField(unique=True, max_length=10)
    link = models.URLField()
    json = JSONField(null=True)

    class Meta:
        verbose_name = 'version'
        verbose_name_plural = 'versions'

    def __str__(self):
        return self.name
