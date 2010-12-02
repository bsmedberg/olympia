# -*- coding: utf-8 -*-
import hashlib
import os
import shutil
import tempfile

from django import forms
from django.conf import settings

import path
import test_utils
from nose.tools import eq_

import amo.utils
from addons.models import Addon
from applications.models import Application, AppVersion
from files.models import File, FileUpload, Platform
from files.utils import parse_xpi, WorkingZipFile
from versions.models import Version


class UploadTest(test_utils.TestCase):
    """
    Base for tests that mess with file uploads, safely using temp directories.
    """

    def setUp(self):
        self._addons_path = settings.ADDONS_PATH
        settings.ADDONS_PATH = tempfile.mkdtemp()
        self._rename = path.path.rename
        path.path.rename = path.path.copy

    def tearDown(self):
        shutil.rmtree(settings.ADDONS_PATH)
        settings.ADDONS_PATH = self._addons_path
        path.path.rename = self._rename

    def xpi_path(self, name):
        path = 'apps/files/fixtures/files/%s.xpi' % name
        return os.path.join(settings.ROOT, path)


class TestFile(test_utils.TestCase):
    """
    Tests the methods of the File model.
    """

    fixtures = ('base/addon_3615', 'base/addon_5579')

    def test_get_absolute_url(self):
        f = File.objects.get(id=67442)
        url = f.get_absolute_url(src='src')
        expected = ('/firefox/downloads/file/67442/'
                    'delicious_bookmarks-2.1.072-fx.xpi?src=src')
        assert url.endswith(expected), url

    def test_delete(self):
        """ Test that when the File object is deleted, it is removed from the
        filesystem """
        file = File.objects.get(pk=67442)
        filename = file.file_path
        if not os.path.exists(os.path.dirname(filename)):
            os.makedirs(os.path.dirname(filename))
        assert not os.path.exists(filename), 'File exists at: %s' % filename
        try:
            open(filename, 'w')
            assert os.path.exists(filename)
            file.delete()
            assert not os.path.exists(filename)
        finally:
            if os.path.exists(filename):
                os.remove(filename)

        # test that the file object can be deleted without the file
        # being present
        file = File.objects.get(pk=74797)
        filename = file.file_path
        assert not os.path.exists(filename), 'File exists at: %s' % filename
        file.delete()

    def test_latest_url(self):
        # With platform.
        f = File.objects.get(id=74797)
        base = '/firefox/downloads/latest/'
        expected = base + '{0}/platform:3/addon-{0}-latest.xpi'
        eq_(expected.format(f.version.addon_id), f.latest_xpi_url())

        # No platform.
        f = File.objects.get(id=67442)
        expected = base + '{0}/addon-{0}-latest.xpi'
        eq_(expected.format(f.version.addon_id), f.latest_xpi_url())

    def test_eula_url(self):
        f = File.objects.get(id=67442)
        eq_(f.eula_url(), '/en-US/firefox/addon/3615/eula/67442')

    def test_generate_filename(self):
        f = File.objects.get(id=67442)
        eq_(f.generate_filename(), 'delicious_bookmarks-2.1.072-fx.xpi')

    def test_generate_filename_platform_specific(self):
        f = File.objects.get(id=67442)
        f.platform_id = amo.PLATFORM_MAC.id
        eq_(f.generate_filename(), 'delicious_bookmarks-2.1.072-fx-mac.xpi')

    def test_generate_filename_many_apps(self):
        f = File.objects.get(id=67442)
        f.version.compatible_apps = (amo.FIREFOX, amo.THUNDERBIRD)
        eq_(f.generate_filename(), 'delicious_bookmarks-2.1.072-fx+tb.xpi')

    def test_generate_filename_ja(self):
        f = File()
        f.version = Version(version='0.1.7')
        f.version.compatible_apps = (amo.FIREFOX,)
        f.version.addon = Addon(name=u' フォクすけといっしょ')
        eq_(f.generate_filename(),
            u'\u30d5\u30a9\u30af\u3059\u3051\u3068\u3044\u3063\u3057\u3087'
            '-0.1.7-fx.xpi')


class TestParseXpi(test_utils.TestCase):
    fixtures = ['base/apps']

    def setUp(self):
        for version in ('3.0', '3.6.*'):
            AppVersion.objects.create(application_id=1, version=version)

    def parse(self, addon=None, filename='extension.xpi'):
        path = 'apps/files/fixtures/files/' + filename
        xpi = os.path.join(settings.ROOT, path)
        return parse_xpi(xpi, addon)

    def test_parse_basics(self):
        # Everything but the apps
        exp = {'guid': 'guid@xpi',
               'name': 'xpi name',
               'description': 'xpi description',
               'version': '0.1',
               'homepage': 'http://homepage.com',
               'type': 1}
        parsed = self.parse()
        for key, value in exp.items():
            eq_(parsed[key], value)

    def test_parse_apps(self):
        exp = (amo.FIREFOX,
               amo.FIREFOX.id,
               AppVersion.objects.get(version='3.0'),
               AppVersion.objects.get(version='3.6.*'))
        eq_(self.parse()['apps'], [exp])

    def test_parse_apps_bad_appver(self):
        AppVersion.objects.all().delete()
        eq_(self.parse()['apps'], [])

    def test_parse_apps_bad_guid(self):
        Application.objects.all().delete()
        eq_(self.parse()['apps'], [])

    def test_guid_match(self):
        addon = Addon.objects.create(guid='guid@xpi', type=1)
        eq_(self.parse(addon)['guid'], 'guid@xpi')

    def test_guid_nomatch(self):
        addon = Addon.objects.create(guid='xxx', type=1)
        with self.assertRaises(forms.ValidationError) as e:
            self.parse(addon)
        eq_(e.exception.messages, ["GUID doesn't match add-on"])

    def test_guid_dupe(self):
        Addon.objects.create(guid='guid@xpi', type=1)
        with self.assertRaises(forms.ValidationError) as e:
            self.parse()
        eq_(e.exception.messages, ['Duplicate GUID found.'])

    def test_match_type(self):
        addon = Addon.objects.create(guid='guid@xpi', type=4)
        with self.assertRaises(forms.ValidationError) as e:
            self.parse(addon)
        eq_(e.exception.messages,
            ["<em:type> doesn't match add-on"])

    def test_unknown_app(self):
        data = self.parse(filename='theme-invalid-app.jar')
        eq_(data['apps'], [])

    # parse_dictionary
    # parse_theme
    # parse_langpack
    # parse_search_engine?


class TestFileUpload(UploadTest):

    def setUp(self):
        super(TestFileUpload, self).setUp()
        self.data = 'file contents'

    def upload(self):
        # The data should be in chunks.
        data = list(amo.utils.chunked(self.data, 3))
        return FileUpload.from_post(data, 'filename.xpi',
                                    len(self.data))

    def test_from_post_write_file(self):
        eq_(open(self.upload().path).read(), self.data)

    def test_from_post_filename(self):
        eq_(self.upload().name, 'filename.xpi')

    def test_from_post_hash(self):
        hash = hashlib.sha256(self.data).hexdigest()
        eq_(self.upload().hash, 'sha256:%s' % hash)


class TestFileFromUpload(UploadTest):
    fixtures = ['base/apps']

    def setUp(self):
        super(TestFileFromUpload, self).setUp()
        appver = {amo.FIREFOX: ['3.0', '3.6', '3.6.*', '4.0b6'],
                  amo.MOBILE: ['0.1', '2.0a1pre']}
        for app, versions in appver.items():
            for version in versions:
                AppVersion(application_id=app.id, version=version).save()
        self.platform = Platform.objects.create(id=amo.PLATFORM_MAC.id)
        self.addon = Addon.objects.create(guid='guid@jetpack',
                                          type=amo.ADDON_EXTENSION,
                                          name='xxx')
        self.version = Version.objects.create(addon=self.addon)

    def upload(self, name):
        d = dict(path=self.xpi_path(name), name='%s.xpi' % name,
                 hash='sha256:%s' % name)
        return FileUpload.objects.create(**d)

    def test_is_jetpack(self):
        upload = self.upload('jetpack')
        f = File.from_upload(upload, self.version, self.platform)
        assert File.objects.get(id=f.id).jetpack

    def test_filename(self):
        upload = self.upload('jetpack')
        f = File.from_upload(upload, self.version, self.platform)
        eq_(f.filename, 'xxx-0.1-mac.xpi')


class TestZip(test_utils.TestCase):

    def test_zip(self):
        # This zip contains just one file chrome/ that we expect
        # to be unzipped as a directory, not a file.
        xpi = os.path.join(os.path.dirname(__file__), 'fixtures',
                           'files', 'directory-test.xpi')

        # This is to work around: http://bugs.python.org/issue4710
        # which was fixed in Python 2.6.2. If the required version
        # of Python for zamboni goes to 2.6.2 or above, this can
        # be removed.
        try:
            dest = tempfile.mkdtemp()
            WorkingZipFile(xpi).extractall(dest)
            assert os.path.isdir(os.path.join(dest, 'chrome'))
        finally:
            shutil.rmtree(dest)
