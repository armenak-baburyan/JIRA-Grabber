from django.contrib import admin

from .models import Issue, Attachment, Version


class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('uid', 'issue', 'filename', )
    raw_id_fields = ('issue',)
    search_fields = ('filename',)


class IssueAdmin(admin.ModelAdmin):
    search_fields = ('key', 'uid_dest')
    list_display = ('key', 'uid', 'uid_dest', 'link', 'link_dest')


class VersionAdmin(admin.ModelAdmin):
    list_display = ('name', 'uid', 'link')


for model, model_admin in (
        (Attachment, AttachmentAdmin),
        (Issue, IssueAdmin),
        (Version, VersionAdmin),
):
    admin.site.register(model, model_admin)
