#!/usr/bin/env python2
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
''' This script implements a simple Calibre Client protocol to
    communicate with the Calibre Wireless Server over WiFi.

    It was inspired by:
    https://github.com/koreader/koreader/blob/master/plugins/calibrecompanion.koplugin/main.lua

    Note that Calibre Companion(CC) is a trade mark held by MultiPie Ltd. The
    Android app Calibre Companion provided by MultiPie is closed-source. This
    plugin only implements a subset function of CC according to the open-source
    smart device driver from Calibre source tree. (More details can be found at
    calibre/devices/smart_device_app/driver.py.

    NOTE: This is a 'hack script' to sync books and metadata from Calibre to
    KyBook 2. It is NOT automatic and requires some user input.
    Currently, the sync is only one-way (Calibre to KyBook 2).
    It has been tested successfully on a Calibre library of 500+ books (12GB),
    but may not work for you.
    There is currently nothing in this script that will change anything in 
    Calibre.
'''
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)
import socket
import select
import traceback
import time
import json
import os
import plistlib
from datetime import datetime
from errno import EAGAIN, EINTR
import inspect
# import subprocess # Used for getting uuid of a disk (for consistency)
# TODO: consider using import ntpath, which will allow basename to work on
# Windows paths.
# import ntpath
import hashlib
from base64 import b64decode
import uuid
import PyPDF2
import signal
import sys

BASE_DIR = '~/KyBook2/'
DEBUG = False
EXTRA_DEBUG = False
# Apple's Cocoa timestamps appear to be from 1/1/2001, so we'll offset ours.
COCOA = datetime(2001, 1, 1)

TEMPLATE_RECORD = {
    "format": "",
    "uid": "",
    "kybid": "",
    "userRating": 0,
    "size": 0,
    "created": (datetime.now() - COCOA).total_seconds(),
    "locations": [
        {
            "uid": "",
            "storage": "Books",
            "file": "",
            "size": 0,
            "created": int((datetime.now() - COCOA).total_seconds()),
            "modified": int((datetime.now() - COCOA).total_seconds()),
            "kind": 0
        }
    ],
    "modified": 0,
    "metadata": {
        "coverUrl": "",
        "publisher": "",
        "lang": "",
        "link": "",
        "title": "",
        "annotation": "",
        "dnLink": "",
        "subjects": [],
        "wordCount": 0,
        "keywords": "",
        "cover": "1",
        "authors": [],
        "sequenceNumber": 0,
        "pageCount": 0,
        "coverHeight": 0,
        "coverColor": 0,
        "date": "",
        "coverWidth": 0,
        "subtitle": "",
        "isbn": "",
        "extKey": "",
        "libid": ""
    },
    "readingTime": 0,
    "readState": 0
}


class XMLConfig(dict):

    '''
    Similar to:class:`DynamicConfig`, except that it uses an XML storage
    backend instead of a pickle file.
    See `https://docs.python.org/dev/library/plistlib.html`_ for the supported
    data types.
    '''

    EXTENSION = '.plist'
    CONFIG_DIR_MODE = 0o700
    config_dir = './'

    def __init__(self, rel_path_to_cf_file, base_path=config_dir):
        dict.__init__(self)
        self.no_commit = False
        self.defaults = {}
        self.file_path = os.path.join(base_path,
                                      *(rel_path_to_cf_file.split('/')))
        self.file_path = os.path.abspath(self.file_path)
        if not self.file_path.endswith(self.EXTENSION):
            self.file_path += self.EXTENSION

        self.refresh()

    def mtime(self):
        ''' mtime '''
        try:
            return os.path.getmtime(self.file_path)
        except EnvironmentError:
            return 0

    def touch(self):
        ''' touch '''
        try:
            os.utime(self.file_path, None)
        except EnvironmentError:
            pass

    def raw_to_object(self, raw):
        ''' raw_to_object '''
        return plistlib.readPlistFromString(raw)

    def to_raw(self):
        ''' to_raw '''
        return plistlib.writePlistToString(self)

    def decouple(self, prefix):
        ''' decouple '''
        self.file_path = os.path.join(
            os.path.dirname(
                self.file_path), prefix + os.path.basename(self.file_path))
        self.refresh()

    def refresh(self, clear_current=True):
        ''' refresh '''
        dic = {}
        if os.path.exists(self.file_path):
            with ExclusiveFile(self.file_path) as infile:
                raw = infile.read()
                try:
                    dic = self.raw_to_object(raw) if raw.strip() else {}
                except SystemError:
                    pass
                except:
                    traceback.print_exc()
                    dic = {}
        if clear_current:
            self.clear()
        self.update(dic)

    def __getitem__(self, key):
        try:
            ans = dict.__getitem__(self, key)
            if isinstance(ans, plistlib.Data):
                ans = ans.data
            return ans
        except KeyError:
            return self.defaults.get(key, None)

    def get(self, key, default=None):
        try:
            ans = dict.__getitem__(self, key)
            if isinstance(ans, plistlib.Data):
                ans = ans.data
            return ans
        except KeyError:
            return self.defaults.get(key, default)

    def __setitem__(self, key, val):
        if isinstance(val, (bytes, str)):
            val = plistlib.Data(val)
        dict.__setitem__(self, key, val)
        self.commit()

    def set(self, key, val):
        ''' set '''
        self.__setitem__(key, val)

    def __delitem__(self, key):
        try:
            dict.__delitem__(self, key)
        except KeyError:
            pass  # ignore missing keys
        else:
            self.commit()

    def commit(self):
        ''' commit '''
        if self.no_commit:
            return
        if hasattr(self, 'file_path') and self.file_path:
            dpath = os.path.dirname(self.file_path)
            if not os.path.exists(dpath):
                os.makedirs(dpath, mode=self.CONFIG_DIR_MODE)
            with ExclusiveFile(self.file_path) as outfile:
                raw = self.to_raw()
                outfile.seek(0)
                outfile.truncate()
                outfile.write(raw)

    def __enter__(self):
        self.no_commit = True

    def __exit__(self, *args):
        self.no_commit = False
        self.commit()


def to_json(obj):
    ''' to_json '''
    if isinstance(obj, bytearray):
        return {'__class__': 'bytearray',
                '__value__': standard_b64encode(bytes(obj))}
    if isinstance(obj, datetime):
        from calibre.utils.date import isoformat
        return {'__class__': 'datetime',
                '__value__': isoformat(obj, as_utc=True)}
    raise TypeError(repr(obj) + ' is not JSON serializable')


def from_json(obj):
    ''' from_json '''
    if '__class__' in obj:
        if obj['__class__'] == 'bytearray':
            return bytearray(standard_b64decode(obj['__value__']))
        if obj['__class__'] == 'datetime':
            from calibre.utils.iso8601 import parse_iso8601
            return parse_iso8601(obj['__value__'], assume_utc=True)
    return obj


class JSONConfig(XMLConfig):
    ''' JSONConfig '''

    EXTENSION = '.json'

    def raw_to_object(self, raw):
        return json.loads(raw.decode('utf-8'), object_hook=from_json)

    def to_raw(self):
        return json.dumps(self, indent=2, default=to_json)

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults[key]

    def get(self, key, default=None):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults.get(key, default)

    def __setitem__(self, key, val):
        dict.__setitem__(self, key, val)
        self.commit()


class CalibreClient():
    """ Implements a client to the SMART_DEVICE_APP driver used by
        Calibre Companion
    """
    name = "calibrecompanion"
    # calibre companion local port
    port = 8134

    # Some network protocol constants
    BASE_PACKET_LEN = 4096
    # PROTOCOL_VERSION            = 1
    MAX_CLIENT_COMM_TIMEOUT = 300.0  # Wait at most N seconds for an answer
    # MAX_UNSUCCESSFUL_CONNECTS   = 5
    #
    # SEND_NOOP_EVERY_NTH_PROBE   = 5
    # DISCONNECT_AFTER_N_SECONDS  = 30*60  # 30 minutes
    #
    # PURGE_CACHE_ENTRIES_DAYS    = 30
    #
    # CURRENT_CC_VERSION          = 128

    # calibre broadcast ports used to find calibre server
    BROADCAST_PORTS = [54982, 48123, 39001, 44044, 59678]

    opcodes = {
        'NOOP': 12,
        'OK': 0,
        'BOOK_DONE': 11,
        'CALIBRE_BUSY': 18,
        'SET_LIBRARY_INFO': 19,
        'DELETE_BOOK': 13,
        'DISPLAY_MESSAGE': 17,
        'FREE_SPACE': 5,
        'GET_BOOK_FILE_SEGMENT': 14,
        'GET_BOOK_METADATA': 15,
        'GET_BOOK_COUNT': 6,
        'GET_DEVICE_INFORMATION': 3,
        'GET_INITIALIZATION_INFO': 9,
        'SEND_BOOKLISTS': 7,
        'SEND_BOOK': 8,
        'SEND_BOOK_METADATA': 16,
        'SET_CALIBRE_DEVICE_INFO': 1,
        'SET_CALIBRE_DEVICE_NAME': 2,
        'TOTAL_SPACE': 4,
    }
    reverse_opcodes = dict([(val, key) for key, val in opcodes.iteritems()])

    def __init__(self):
        # TODO: Add in self.app_version, so we can get update messages from Cal
        self.init_info = {
            "canUseCachedMetadata": True,
            "acceptedExtensions": [
                "azw3", "cbz", "djvu", "epub", "fb2", "mobi", "pdb", "pdf"
            ],
            "canStreamMetadata": True,
            "canAcceptLibraryInfo": True,
            "extensionPathLengths": {
                "azw3": 42,
                "cbz": 42,
                "djvu": 42,
                "epub": 42,
                "fb2": 42,
                "mobi": 42,
                "pdb": 42,
                "pdf": 42,
            },
            "useUuidFileNames": False,
            "passwordHash": "",
            "canReceiveBookBinary": True,
            "maxBookContentPacketLen": 4096,
            "appName": "KyBook 2 Calibre plugin",
            "ccVersionNumber": 106,
            "deviceName": "KyBook 2",
            "canStreamBooks": True,
            "versionOK": True,
            "canDeleteMultipleBooks": True,
            "canSendOkToSendbook": True,
            "coverHeight": 240,
            "cacheUsesLpaths": True,
            "deviceKind": "KyBook 2",
        }
        self.device_info = {
            "device_info": {
                # TODO: we might want a consistent uuid for syncing?
                "device_store_uuid": str(uuid.uuid1()),
                "device_name": "KyBook 2 Calibre Companion",
            },
            "version": 106,
            "device_version": "KyBook 2",
        }
        self.free_space_bytes = {
            "free_space_on_device": 0,
        }
        # The count is calculated in self._get_book_count()
        self.book_count = {
            "willStream": True,
            "willScan": True,
            "count": 0,
        }
        # TODO: self.records seems like a poor name
        self.records = {
            "records": [],
        }
        self.debug_start_time = time.time()
        self.debug_time = time.time()
        self.calibre_socket = None
        self.is_connected = False
        self.cal_init_info = None
        self.cal_dev_info = None
        self.cal_lib_info = None
        self.cal_book_count = None
        self.cal_booklists = None
        # self.files_path = os.path.expanduser(
            # '~/Library/Mobile Documents/iCloud~com~kolyvan~rocket/Documents/')
        # Unfortunately, iCloud free version has a 3GB limit
        self.files_path = os.path.expanduser(BASE_DIR)
        try: 
            os.makedirs(self.files_path + 'App/Backups')
            os.makedirs(self.files_path + 'App/Images')
            os.makedirs(self.files_path + 'Books')
        except OSError:
            if (not os.path.isdir(self.files_path + 'App/Backups') and 
                os.path.isdir(self.files_path + 'App/Images') and 
                os.path.isdir(self.files_path + 'Books')):
                    print("Couldn't make directories in " + self.files_path)
                    self.stop()
        print("Initialization complete.")

    def _debug(self, *args):
        ''' If the global DEBUG is set, print debug info.
        '''
        if not DEBUG:
            return
        total_elapsed = time.time() - self.debug_start_time
        elapsed = time.time() - self.debug_time
        print('SMART_DEV (%7.2f:%7.3f) %s' % (total_elapsed, elapsed,
                                              inspect.stack()[1][3]), end='')
        for arg in args:
            try:
                if isinstance(arg, dict):
                    printable = {}
                    for key, val in arg.iteritems():
                        if isinstance(val, (str, unicode)) and len(val) > 50:
                            printable[key] = 'too long'
                        else:
                            printable[key] = val
                    print('', printable, end='')
                else:
                    print('', arg, end='')
            except Exception:
                print('', 'value too long', end='')
        print()
        self.debug_time = time.time()

    def _get_records(self):
        ''' Read KyBook 2's json file and read the records in it.
        '''
        try:
            with open(
                self.files_path + 'App/Backups/db-' +\
                    datetime.today().strftime('%Y-%m-%d') +\
                    '.json') as infile:
                self.records = json.load(infile)
                print(self.records)
                self._debug(self.records)
                return True
        except:
            print('Unable to get records from ' + self.file_path)
            # TODO: Change this, otherwise KyBook 2 data will be overwritten
            pass
            # exit(1)

    def _startup_on_demand(self):
        ''' Make the connection to Calibre and then process incoming data.

            We send "hello" to the UDP ports Calibre listens on. Calibre then
            sends us the TCP port and we connect to that.
        '''
        message = None
        host = None
        port = None
        print("Finding Calibre TCP port ...", end='')
        # Use UDP to request the TCP port
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp_socket.settimeout(3)
        except socket.error as exc:
            message = 'failed to create UDP socket.'
            self._debug(message, exc)
            return message
        for broadcast_port in self.BROADCAST_PORTS:
            try:
                self._debug('Trying broadcast port', broadcast_port)
                udp_socket.sendto("Hello", ('<broadcast>', broadcast_port))
                data, addr = udp_socket.recvfrom(1024)
                if data and addr:
                    host = addr[0]
                    port = int(data.split(',', 1)[1])
                    self._debug(host, port)
                    break
            except socket.error as exc:
                message = 'failed to receive TCP host and port from Calibre.'
                self._debug(message, exc)
                return message
        print(" done.\nConnecting to TCP port ...")
        try:
            self.calibre_socket = socket.socket(
                socket.AF_INET, socket.SOCK_STREAM)
            self.calibre_socket.settimeout(3)
            self.calibre_socket.connect((host, int(port)))
            self.calibre_socket.setblocking(0)
        except socket.error as exc:
            message = 'failed to connect to Calibre on ' + host + ':' + port 
            self._debug(message, host, port, exc)
            return message
        print("Connected to Calibre server at %s:%s" % (host, port))
        while True:
            opcode = None
            # time.sleep(30)
            ans = select.select([self.calibre_socket],
                                [], [], 0)
            if len(ans[0]) > 0:
                try:
                    opcode, result = self._receive_from_server()
                    self._debug(opcode, result)
                except TypeError:
                    # Calibre has disconnected, we might as well exit
                    sys.exit(0)
                    
            # if len(ans[1]) > 0 and opcode:
            # We've got an opcode and data from calibre.
            # Call the eponymous (lowercased) method prefixed with _ans_
            # So, opcode 'NOOP' calls _ans_noop(result)
            if opcode:
                function = getattr(self, '_ans_' + opcode.lower())
                function(result)

    # These methods answer set/get calls from Calibre, so they ANSWER (_ans_)
    def _ans_get_initialization_info(self, json_data):
        ''' Prepare and send back intialization data required by Calibre. '''
        self._debug(json_data)
        print("Sending initialization info.")
        self.cal_init_info = json_data
        self._json_to_file(json_data)
        self._call_server('OK', self.init_info)

    def _ans_get_device_information(self, json_data):
        ''' Prepare and send back device data required by Calibre. '''
        self._debug(json_data)
        print("Sending device info.")
        self._call_server('OK', self.device_info)

    def _ans_set_calibre_device_info(self, json_data):
        ''' Save and acknowledge data sent by Calibre. '''
        self._debug(json_data)
        print("Setting Calibre info.")
        self.cal_dev_info = json_data
        self._json_to_file(json_data)
        # TODO: save uuid setting somewhere
        # G_reader_settings:saveSetting("device_store_uuid",
        # arg.device_store_uuid)
        self._call_server('OK', {})

    def _ans_noop(self, json_data):
        ''' Acknowledge NOOP sent by Calibre. '''
        self._debug(json_data)
        # TODO: there seems to be but json_data.get('count') doesn't work.
        if 'count' not in json_data:
            self._call_server('OK', {})

    def _ans_free_space(self, json_data):
        ''' Prepare and send back free_space data required by Calibre.

            We provide the number of free bytes that ordinary users are 
            allowed to use (i.e., excl. reserved space)
        '''
        self._debug(json_data)
        print("Sending free space info.")
        # TODO: Change this path?
        statvfs = os.statvfs('/')
        free_space = statvfs.f_frsize * statvfs.f_bavail
        self.free_space_bytes['free_space_on_device'] = free_space
        self._call_server('OK', self.free_space_bytes)

    def _ans_set_library_info(self, json_data):
        ''' Save and acknowledge library data sent by Calibre. '''
        self._debug(json_data)
        print("Setting library info.")
        self.cal_lib_info = json_data
        self._json_to_file(json_data)
        self._call_server('OK', {})

    def _ans_get_book_count(self, json_data):
        ''' Prepare and send the number of books on device to Calibre.
            json_data contains info from Calibre:
            canStream (T); canScan (T); willUseCachedMetadata;
            supportsSync (column); canSupportBookFormatSync (T)
        '''
        count = 0
        self._debug(json_data)
        print("Sending book count.")
        self.cal_book_count = json_data
        self._json_to_file(json_data)
        try:
            self._debug(self.records)
            count = len(self.records['records'])
            self.book_count['count'] = count
        except KeyError as exc:
            # There were no records in KyBook 2, so use default value (0)
            pass
        # TODO: If the count > 0, we need to send the book's metadata.
        self._call_server('OK', self.book_count)

    def _ans_send_booklists(self, json_data):
        ''' Send booklists to Calibre.
            (Currently does NOT)
            TODO: Send booklists??
        '''
        self._debug(json_data)
        print("NOT sending booklist. (Not yet implemented.)")
        self.cal_booklists = json_data
        self._json_to_file(json_data)

    def _ans_send_book(self, json_data):
        ''' Receive and save a book sent by Calibre. '''
        self._debug(json_data)
        self._json_to_file(json_data)
        self._call_server('OK', {})
        filename = os.path.basename(json_data['lpath'])
        print("Receiving %s" % filename)
        self._recv_file(filename, json_data['length'])
        sha1 = hashlib.sha1(
            open(self.files_path + 'Books/' + filename, 'rb').read()).hexdigest()
        basename, ext = os.path.splitext(filename)
        no_pages = 0
        if ext == '.pdf':
            reader = PyPDF2.PdfFileReader(
                open(self.files_path + 'Books/' + filename, 'rb'))
            no_pages = reader.getNumPages()
        # TODO: add pagecount for djvu files.
        self._add_to_records(json_data, ext.strip('.'), sha1, no_pages)

    def _add_to_records(self, cal_book, file_ext, uid, page_count):
        ''' Add the book's metadata to the records list from KyBook 2.
        '''
        self._debug()
        print("Adding book metadata to records.")
        # TODO: total_books might be useful when sent multiple books??
        total_books = cal_book['totalBooks']
        # Make a copy of the template for data in Calibre's format
        new_rec = json.loads(json.dumps(TEMPLATE_RECORD))
        # Calibre uses different names, etc., so we need to copy from
        # KyBook 2's data.
        # Entries ordered as they appear in KyBook 2
        # Unless otherwise assigned, use defaults from template
        new_rec['format'] = file_ext
        # Calibre uses a uuid for each book's entry; KyBook 2 will ONLY use
        # the book file's SHA1. This makes it tricky to relate metadata in
        # Calibre with that in KyBook 2. We get round this by saving the book's
        # cover (KyBook)/thumbnail (Calibre) as uuid.jpg. (We want Calibre's
        # thumbnails anyway as some books have a first page that is not a good
        # cover and KyBook 2 would just use the first page.)
        new_rec['uid'] = uid
        new_rec['size'] = cal_book['length']
        date = cal_book['metadata'].get('timestamp', None)
        if date and not date == 'None':
            try:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%f+00:00")
            new_rec['locations'][0]['created'] = int(
                (stamp - COCOA).total_seconds())
        new_rec['locations'][0]['file'] = '/' + cal_book['lpath']
        date = cal_book['metadata'].get('last_modified', None)
        if date and not date == 'None':
            try:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%f+00:00")
            new_rec['locations'][0]['modified'] = int(
                (stamp - COCOA).total_seconds())
        new_rec['locations'][0]['size'] = cal_book['length']
        new_rec['locations'][0]['uid'] = uid
        new_rec['metadata']['publisher'] =\
            cal_book['metadata'].get('publisher', '')
        new_rec['metadata']['lang'] = next(iter(
            cal_book['metadata']['languages'] or []), '')
        for key, value in cal_book['metadata']['identifiers'].iteritems():
            # KyBook has a specific field for isbn
            if key == 'isbn':
                new_rec['metadata']['isbn'] = value
            else:
                if not new_rec['metadata'].get('ids'):
                    new_rec['metadata']['ids'] = []
                new_key_val = {'name': value}
                new_rec['metadata']['ids'].append(new_key_val)
        new_rec['metadata']['title'] = cal_book['metadata']['title']
        # KyBook has no comments field, so use annotation.
        new_rec['metadata']['annotation'] = self._remove_html_markup(
            cal_book['metadata'].get('comments', ''))
        # Calibre's tags are mapped to KyBook's subjects (which they are
        # more like anyway. KyBook's 'tags' have a specific use/meaning.)
        for idx, tag_name in enumerate(cal_book['metadata']['tags']):
            new_rec['metadata']['subjects'].append({})
            new_rec['metadata']['subjects'][idx]['title'] = tag_name
            new_rec['metadata']['subjects'][idx]['href'] = ''
        for idx, author_name in enumerate(cal_book['metadata']['authors']):
            new_rec['metadata']['authors'].append({})
            new_rec['metadata']['authors'][idx]['title'] = author_name
            new_rec['metadata']['authors'][idx]['href'] = ''
        new_rec['metadata']['pageCount'] = page_count
        # KyBook 2 uses points as measurement
        # points = no_of_pixels * points_per_inch / dots_per_inch
        # points = no_of_pixels * 72 / 150
        new_rec['metadata']['coverWidth'] = int(
            cal_book['metadata']['thumbnail'][0] * 72 / 150)
        new_rec['metadata']['coverHeight'] = int(
            cal_book['metadata']['thumbnail'][1] * 72 / 150)
        # Decode the cover data and save it out to file. KyBook 2's field
        # metadata.cover uses userimage:file_location to refer to a (optional)
        # file to use as the cover. We use (Calibre's) book metadata uuid as
        # the file name, so that we can refer back to Calibre.
        cover = b64decode(cal_book['metadata']['thumbnail'][2])
        cal_book_uuid = cal_book['metadata']['uuid']
        try:
            with open(self.files_path + \
                'App/Images/' + cal_book_uuid + '.jpg', 'w+b') as outfile:
                outfile.write(cover)
        except Exception as exc:
            print('Unable to save cover to ' + self.files_path + str(exc))
            # TODO: Think about this. Just carry on or fail?
            pass
        new_rec['metadata']['cover'] = 'userimage:' + cal_book_uuid + '.jpg'
        # KyBook's date field appears to be publication date.
        date = cal_book['metadata'].get('pubdate')
        if date and not date == 'None':
            try:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S+00:00")
            except ValueError:
                stamp = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%f+00:00")
            new_rec['metadata']['date'] = (stamp - COCOA).total_seconds()
        self.records['records'].append(new_rec)
        try:
            with open(self.files_path + 'App/Backups/db-' + datetime.today().strftime(
                    '%Y-%m-%d') + '.json', 'w') as outfile:
                json.dump(self.records, outfile)
                return True
        except Exception as exc:
            print('Unable to save records to ' + self.files_path + str(exc))
            # TODO: Think about this. Just carry on or fail?
            pass

    def _recv_file(self, filename, length):
        ''' Receive a file from Calibre and save to disk.

            This make take some time to arrive, so we give the user some
            feedback on progress.
        '''
        self._debug()
        eof = False
        position = 0
        outfile = open(self.files_path + 'Books/' + filename, 'w+b')
        self._debug(length, eof, position, outfile)
        remaining = length
        while remaining > 0:
            percent = int((length - remaining) / length * 100)
            print('\rReceived: ' + str(percent) + ' %', end='')
            vec = self._read_binary_from_net(
                min(remaining, self.BASE_PACKET_LEN))
            outfile.write(vec)
            remaining -= len(vec)
        # The space after 'Done.' is needed to overwrite the previous message.
        print('\rDone.          ')
        outfile.close()
        self._debug(position, eof)

    # JSON booklist encode & decode

    def _json_encode(self, opcode, arg):
        ''' If the argument is a booklist or contains a book, use the metadata
            json codec to first convert it to a string dict
        '''
        self._debug()
        res = {}
        for key, val in arg.iteritems():
            # if isinstance(val, (Book, Metadata)):
            #     res[key] = self.json_codec.encode_book_metadata(val)
            #     series = val.get('series', None)
            #     if series:
            #         tsorder = tweaks['save_template_title_series_sorting']
            #         series = title_sort(series, order=tsorder)
            #     else:
            #         series = ''
            #     self._debug('series sort = ', series)
            #     res[key]['_series_sort_'] = series
            # else:
            res[key] = val
        # from calibre.utils.config import to_json
        return json.dumps([opcode, res], encoding='utf-8', default=to_json)

    def _json_to_file(self, json_data):
        ''' This is a debug method to save data to file so we can inspect it.
        '''
        if not DEBUG and not EXTRA_DEBUG:
            return
        with open(inspect.stack()[1][3] + '.json', 'w') as outfile:
            json.dump(json_data, outfile)

    # Network functions

    def _read_binary_from_net(self, length):
        try:
            self.calibre_socket.settimeout(self.MAX_CLIENT_COMM_TIMEOUT)
            vec = self.calibre_socket.recv(length)
            self.calibre_socket.settimeout(None)
            return vec
        except:
            self._close_calibre_socket()
            raise

    def _read_string_from_net(self):
        data = bytes(0)
        while True:
            dex = data.find(b'[')
            if dex >= 0:
                break
            # recv seems to return a pointer into some internal buffer.
            # Things get trashed if we don't make a copy of the data.
            vec = self._read_binary_from_net(2)
            if len(vec) == 0:
                # documentation says the socket is broken permanently.
                return ''
            data += vec
        total_len = int(data[:dex])
        data = data[dex:]
        pos = len(data)
        while pos < total_len:
            vec = self._read_binary_from_net(total_len - pos)
            if len(vec) == 0:
                # documentation says the socket is broken permanently.
                return ''
            data += vec
            pos += len(vec)
        return data

    def _send_byte_string(self, sock, string):
        if not isinstance(string, bytes):
            self._debug('given a non-byte string!')
            self._close_calibre_socket()
            print("Internal error: found a string that isn't bytes")
        sent_len = 0
        total_len = len(string)
        while sent_len < total_len:
            try:
                sock.settimeout(self.MAX_CLIENT_COMM_TIMEOUT)
                if sent_len == 0:
                    amt_sent = sock.send(string)
                else:
                    amt_sent = sock.send(string[sent_len:])
                sock.settimeout(None)
                if amt_sent <= 0:
                    raise IOError('Bad write on socket')
                sent_len += amt_sent
            except socket.error as exc:
                self._debug('socket error', exc, exc.errno)
                if exc.args[0] != EAGAIN and exc.args[0] != EINTR:
                    self._close_calibre_socket()
                    raise
                time.sleep(0.1)  # lets not hammer the OS too hard
            except:
                self._close_calibre_socket()
                raise

    def _call_server(self, opname, arg, print_debug_info=True):
        extra_debug = EXTRA_DEBUG
        if print_debug_info or extra_debug:
            if extra_debug:
                self._debug(opname, arg)
            else:
                self._debug(opname)
        if self.calibre_socket is None:
            return None, None
        try:
            string = self._json_encode(self.opcodes[opname], arg)
            if print_debug_info and extra_debug:
                self._debug('send string', string)
            self._send_byte_string(self.calibre_socket,
                                   (b'%d' % len(string)) + string)
        except socket.timeout:
            self._debug('timeout communicating with Calibre')
            self._close_calibre_socket()
            raise TimeoutError('Calibre did not respond in reasonable time')
        except socket.error:
            self._debug('Calibre went away')
            self._close_calibre_socket()
            raise ControlError(desc='Calibre closed the network connection')
        except:
            self._debug('other exception')
            traceback.print_exc()
            self._close_calibre_socket()
            raise

    def _receive_from_server(self, print_debug_info=True):
        extra_debug = EXTRA_DEBUG
        try:
            vec = self._read_string_from_net()
            if print_debug_info and extra_debug:
                self._debug('received string', vec)
            if vec:
                vec = json.loads(vec, object_hook=from_json)
                if print_debug_info and extra_debug:
                    self._debug('receive after decode')  # , v)
                return (self.reverse_opcodes[vec[0]], vec[1])
            self._debug('protocol error -- empty json string')
            raise socket.error
        except socket.timeout:
            self._debug('timeout communicating with Calibre')
            self._close_calibre_socket()
            # raise TimeoutError('Calibre did not respond in reasonable time')
        except socket.error:
            self._debug('Calibre went away')
            self._close_calibre_socket()
            # raise ControlError(desc='Calibre closed the network connection')
        except:
            self._debug('other exception')
            traceback.print_exc()
            self._close_calibre_socket()
            raise

    def _close_socket(self, the_socket):
        ''' Force close a socket. The shutdown permits the close even if data
            transfer is in progress.
        '''
        self._debug()
        try:
            the_socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            # the shutdown can fail if the socket isn't fully connected.
            # Ignore it
            pass
        the_socket.close()

    def _close_calibre_socket(self):
        if self.calibre_socket is not None:
            try:
                self._close_socket(self.calibre_socket)
            except socket.error:
                pass
            self.calibre_socket = None
        self.is_connected = False

    def start(self):
        ''' start '''
        self._debug()
        signal.signal(signal.SIGINT, self._signal_handler)
        return self._startup_on_demand()

    def stop(self):
        ''' stop '''
        self._debug()
        self._close_calibre_socket()
        sys.exit(0)

    def _signal_handler(self, sig, frame):
        ''' Handle the user stopping the script with ctrl-c. '''
        print('User requested exit.')
        self.stop()



    # Utilities
    def _remove_html_markup(self, string):
        ''' A quick and dirty attempt to remove HTML markup from comments.
            (KyBook 2, doesn't support HTML in annotations, so we might as
            well remove it.)
        '''
        tag = False
        quote = False
        out = ""
        if not string:
            return out
        for char in string:
            if char == '<' and not quote:
                tag = True
            elif char == '>' and not quote:
                tag = False
            elif (char == '"' or char == "'") and tag:
                quote = not quote
            elif not tag:
                out = out + char
        return out


def main():
    ''' Set a signal to receive ctrl-c from the user.
         Start up and connect to Calibre.
    '''
    cct = CalibreClient()
    msg = cct.start()
    if msg:
        print("\nFailed to connect to Calibre: " + msg)
        print("\n\nStart Calibre and:")
        print("1. select Connect/share")
        print("2. select Start wireless device connection")
        print("3. select OK.")


if __name__ == '__main__':
    main()
