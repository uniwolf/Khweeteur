#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2010 Benoît HERVIER
# Copyright (c) 2011 Neal H. Walfield
# Licenced under GPLv3

from __future__ import with_statement

import sys
import time
from PySide.QtCore import Slot, QTimer, QCoreApplication
import atexit
import os
import shutil
from signal import SIGTERM
import random

import logging

from posttweet import post_tweet
from retriever import KhweeteurRefreshWorker
from settings import SUPPORTED_ACCOUNTS, settings_db, accounts

import cPickle as pickle
import re

from qwidget_gui import __version__

import dbus

import dbus.mainloop.glib

#from dbus.mainloop.qt import DBusQtMainLoop
#DBusQtMainLoop(set_as_default=True)

import twitter
import glob

try:
    from PIL import Image
except:
    import Image

import os.path
import dbus.service

from pydaemon.runner import DaemonRunner
from lockfile import LockTimeout

try:
    from QtMobility.Location import QGeoPositionInfoSource
except:
    print 'Pyside QtMobility not installed or broken'

# A hook to catch errors

def install_excepthook(version):
    '''Install an excepthook called at each unexcepted error'''

    __version__ = version

    def my_excepthook(exctype, value, tb):
        '''Method which replace the native excepthook'''

        # traceback give us all the errors information message like
        # the method, file line ... everything like
        # we have in the python interpreter

        import traceback
        trace_s = ''.join(traceback.format_exception(exctype, value, tb))
        print 'Except hook called : %s' % trace_s
        formatted_text = '%s Version %s\nTrace : %s' % ('Khweeteur',
                __version__, trace_s)
        logging.error(formatted_text)

    sys.excepthook = my_excepthook

class KhweeteurDBusHandler(dbus.service.Object):

    def __init__(self):
        dbus.service.Object.__init__(self, dbus.SessionBus(),
                                     '/net/khertan/Khweeteur')
        self.m_id = 0

    def info(self, message):
        '''Display an information banner'''

        logging.debug('Dbus.info(%s)' % message)

        message = message.replace('\'', '')
        message = message.replace('<', '')
        message = message.replace('>', '')

        if message == '':
            import traceback
            (exc_type, exc_value, exc_traceback) = sys.exc_info()
            logging.error('Null info message : %s'
                          % repr(traceback.format_exception(exc_type,
                          exc_value, exc_traceback)))

        try:
            m_bus = dbus.SystemBus()
            m_notify = m_bus.get_object('org.freedesktop.Notifications',
                                        '/org/freedesktop/Notifications')
            iface = dbus.Interface(m_notify, 'org.freedesktop.Notifications')
            if not message.startswith('Khweeteur'):
                message = 'Khweeteur : ' + message
            iface.SystemNoteInfoprint(message)
        except:
            import traceback
            (exc_type, exc_value, exc_traceback) = sys.exc_info()
            logging.error('Error info message : %s'
                          % repr(traceback.format_exception(exc_type,
                          exc_value, exc_traceback)))

    @dbus.service.method(dbus_interface='net.khertan.Khweeteur',
                         in_signature='', out_signature='b')
    def isRunning(self):
        return True
        
    @dbus.service.signal(dbus_interface='net.khertan.Khweeteur', signature='')
    def refresh_ended(self):
        pass

    @dbus.service.signal(dbus_interface='net.khertan.Khweeteur', signature='us')
    def new_tweets(self, count, ttype):
        logging.debug('New tweet notification ttype : %s (%s); count: %d'
                      % (ttype, str(type(ttype)), count))

        if count == 0:
            return

        settings = settings_db()
        #.value('showNotifications') == '2':                      

        if ((ttype == 'Mentions') and
            (settings.value('showDMNotifications') == '2')) \
            or ((ttype == 'DMs') and
                (settings.value('showDMNotifications') == '2')):
            m_bus = dbus.SystemBus()
            m_notify = m_bus.get_object('org.freedesktop.Notifications',
                                        '/org/freedesktop/Notifications')
            iface = dbus.Interface(m_notify, 'org.freedesktop.Notifications')

            if ttype == 'DMs' :
                msg = 'New DMs'
            elif ttype == 'Mentions':
                msg = 'New mentions'
            else:
                msg = 'New tweets'
            try:
                self.m_id = iface.Notify(
                    'Khweeteur',
                    self.m_id,
                    'khweeteur',
                    msg,
                    msg,
                    ['default', 'call'],
                    {
                        'category': 'khweeteur-new-tweets',
                        'desktop-entry': 'khweeteur',
                        'dbus-callback-default'
                            : 'net.khertan.khweeteur /net/khertan/khweeteur net.khertan.khweeteur show_now'
                            ,
                        'count': count,
                        'amount': count,
                        },
                    -1,
                    )
            except Exception:
                logging.exception("Displaying new-tweets info banner")


class KhweeteurDaemon(QCoreApplication):
    def __init__(self):
        pass

    def run(self):
        QCoreApplication.__init__(self,sys.argv)
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            from PySide import __version_info__ as __pyside_version__
        except:
            __pyside_version__ = None
        try:
            from PySide.QtCore import __version_info__ as __qt_version__
        except:
            __qt_version__ = None

#        app = QCoreApplication(sys.argv)

        logging.basicConfig(
            level=logging.DEBUG,
            format=(str(os.getpid()) + ' '
                    + '%(asctime)s %(levelname)-8s %(message)s'),
            datefmt='%a, %d %b %Y %H:%M:%S',
            filename=os.path.expanduser('~/.khweeteur.log'), filemode='w')
        logging.info('Starting daemon %s' % __version__)
        logging.info('PySide version %s' % repr(__pyside_version__))
        logging.info('Qt Version %s' % repr(__qt_version__))

        try:
            os.nice(10)
        except:
            pass
            
        self.bus = dbus.SessionBus()
        self.bus.add_signal_receiver(self.update, path='/net/khertan/Khweeteur'
                                     , dbus_interface='net.khertan.Khweeteur',
                                     signal_name='require_update')
        self.bus.add_signal_receiver(post_tweet,
                                     path='/net/khertan/Khweeteur',
                                     dbus_interface='net.khertan.Khweeteur',
                                     signal_name='post_tweet')
        # We maintain the list of currently running jobs so that we
        # know when all jbos complete, to improve debugging output,
        # and because we have to: if the QThread object goes out of
        # scope and is gced while the thread is still running, Bad
        # Things Happen.
        self.threads = {}
        self.idtag = None

        #On demand geoloc
        self.geoloc_source = None
        self.geoloc_coordinates = None

        # Cache Folder

        self.cache_path = os.path.join(os.path.expanduser('~'), '.khweeteur',
                                       'cache')
        if not os.path.exists(self.cache_path):
            os.makedirs(self.cache_path)

        # Post Folder

        self.post_path = os.path.join(os.path.expanduser('~'), '.khweeteur',
                                      'topost')
        if not os.path.exists(self.post_path):
            os.makedirs(self.post_path)

        self.dbus_handler = KhweeteurDBusHandler()
                
        # mainloop = DBusQtMainLoop(set_as_default=True)

        settings = settings_db()
        if not settings.contains('refresh_interval'):
            refresh_interval = 600
        else:
            refresh_interval = int(settings.value('refresh_interval')) * 60

        self.utimer = QTimer()
        self.utimer.timeout.connect(self.update)
        self.utimer.start(refresh_interval * 1000)
                
        QTimer.singleShot(200, self.update)

        # gobject.timeout_add_seconds(refresh_interval, self.update, priority=gobject.PRIORITY_LOW)

        logging.debug('Timer added')
        self.exec_()
        logging.debug('Daemon stop')

    def geolocStart(self):
        '''Start the GPS with a 50000 refresh_rate'''
        self.geoloc_coordinates = None
        if self.geoloc_source is None:
            try:
                self.geoloc_source = \
                    QGeoPositionInfoSource.createDefaultSource(None)
            except:
                self.geoloc_source = None
                self.geoloc_coordinates = (0,0)
                print 'PySide QtMobility not installed or package broken'
            if self.geoloc_source is not None:
                self.geoloc_source.setUpdateInterval(50000)
                self.geoloc_source.positionUpdated.connect(self.geolocUpdated)
                self.geoloc_source.startUpdates()

    def geolocStop(self):
        '''Stop the GPS'''

        self.geoloc_coordinates = None
        if self.geoloc_source is not None:
            self.geoloc_source.stopUpdates()
            self.geoloc_source = None

    def geolocUpdated(self, update):
        '''GPS Callback on update'''

        if update.isValid():
            self.geoloc_coordinates = (update.coordinate().latitude(),
                                       update.coordinate().longitude())
            self.update()
            self.geolocStop()
        else:
            print 'GPS Update not valid'

    def do_posts(self):
        """
        Post any queued posts.
        """
        settings = settings_db()

        items = glob.glob(os.path.join(self.post_path, '*'))

        if len(items)>0:
            if (settings.value('useGPS')=='2') and (settings.value('useGPSOnDemand')=='2'):
                if self.geoloc_source == None:
                    self.geolocStart()
                if self.geoloc_coordinates == None:
                    return

        for item in items:
            self.do_post(item, settings)

    def do_post(self, item, settings=None):
        """
        Post a status update.  item is the name of a file containing a
        pickled status update.
        """
        if settings is None:
            settings = settings_db()

        try:
            with open(item, 'rb') as fhandle:
                post = pickle.load(fhandle)

            logging.debug('Posting %s: %s' % (item, repr(post)))

            text = post['text']
            if post['shorten_url'] == 1:
                urls = re.findall("(?:^|\s)(?P<url>https?://[^\s]+)", text)
                if len(urls) > 0:
                    import bitly
                    a = bitly.Api(login='pythonbitly',
                            apikey='R_06871db6b7fd31a4242709acaf1b6648')

                    for url in urls:
                        try:
                            short_url = a.shorten(url)
                            text = text.replace(url, short_url)
                        except:
                            pass

            if (settings.value('useGPS')=='2') and (settings.value('useGPSOnDemand')=='2'):
                post['latitude'], post['longitude'] = \
                    self.geoloc_coordinates

            if not post['latitude']:
                post['latitude'] = None
            else:
                post['latitude'] = int(post['latitude'])
            if not post['longitude']:
                post['longitude'] = None
            else:
                post['longitude'] = int(post['longitude'])

            # Loop on accounts
            did_something = False
            for account in accounts():
                # Reply

                acted = True
                if post['action'] == 'reply':  # Reply tweet
                    if account.uuid == post['base_url']:
                        api = account.api
                        if post['serialize'] == 1:
                            api.PostSerializedUpdates(text,
                                    in_reply_to_status_id=int(post['tweet_id'
                                    ]), latitude=post['latitude'],
                                    longitude=post['longitude'])
                        else:
                            api.PostUpdate(text,
                                    in_reply_to_status_id=int(post['tweet_id'
                                    ]), latitude=post['latitude'],
                                    longitude=post['longitude'])
                        logging.debug('Posted reply %s : %s' % (text,
                                post['tweet_id']))
                        if settings.contains('ShowInfos'):
                            if settings.value('ShowInfos') == '2':
                                self.dbus_handler.info('Khweeteur: Reply posted to '
                                 + account['name'])
                elif post['action'] == 'retweet':

                    # Retweet

                    if account.uuid == post['base_url']:
                        api = account.api
                        api.PostRetweet(tweet_id=int(post['tweet_id']))
                        logging.debug('Posted retweet %s'
                                % (post['tweet_id'], ))
                        if settings.contains('ShowInfos'):
                            if settings.value('ShowInfos') == '2':
                                self.dbus_handler.info('Khweeteur: Retweet posted to '
                                     + account['name'])
                elif post['action'] == 'tweet':

                    # Else "simple" tweet

                    if account.uuid == post['base_url']:
                        api = account.api
                        if post['serialize'] == 1:
                            api.PostSerializedUpdates(text,
                                    latitude=post['latitude'],
                                    longitude=post['longitude'])
                        else:
                            api.PostUpdate(text,
                                    latitude=post['latitude'],
                                    longitude=post['longitude'])
                        logging.debug('Posted %s' % (text, ))
                        if settings.contains('ShowInfos'):
                            if settings.value('ShowInfos') == '2':
                                self.dbus_handler.info('Khweeteur: Status posted to '
 + account['name'])
                elif post['action'] == 'delete':
                    if account.uuid == post['base_url']:
                        api = account.api
                        api.DestroyStatus(int(post['tweet_id']))
                        path = os.path.join(os.path.expanduser('~'),
                                '.khweeteur', 'cache', 'HomeTimeline',
                                post['tweet_id'])
                        os.remove(path)
                        logging.debug('Deleted %s' % (post['tweet_id'],
                                ))
                        if settings.contains('ShowInfos'):
                            if settings.value('ShowInfos') == '2':
                                self.dbus_handler.info('Khweeteur: Status deleted on '
 + account['name'])
                elif post['action'] == 'favorite':
                    if account.uuid == post['base_url']:
                        api = account.api
                        api.CreateFavorite(int(post['tweet_id']))
                        logging.debug('Favorited %s' % (post['tweet_id'
                                ], ))
                elif post['action'] == 'follow':
                    if account.uuid == post['base_url']:
                        api = account.api
                        friend = api.CreateFriendship(post['tweet_id'])
                        logging.debug('Follow %s (account: %s) -> %s'
                                      % (repr(post), repr(account), friend))
                elif post['action'] == 'unfollow':
                    if account.uuid == post['base_url']:
                        api = account.api
                        friend = api.DestroyFriendship(post['tweet_id'])
                        logging.debug('Unfollow %s (account: %s) -> %s'
                                      % (repr(post), repr(account), friend))
                elif post['action'] == 'twitpic':
                    if account['base_url'] \
                            == SUPPORTED_ACCOUNTS[0]['base_url']:
                        api = account.api
                        import twitpic
                        twitpic_client = \
                            twitpic.TwitPicOAuthClient(consumer_key=api._username,
                                consumer_secret=api._password,
                                access_token=api._oauth_token.to_string(),
                                service_key='f9b7357e0dc5473df5f141145e4dceb0'
                                )
                        params = {}
                        params['media'] = 'file://' + post['base_url']
                        params['message'] = unicode(post['text'])
                        response = twitpic_client.create('upload',
                                params)
                        if 'url' in response:
                            post_tweet(
                                post['shorten_url'],
                                post['serialize'],
                                unicode(response['url']) + u' : '
                                    + post['text'],
                                post['latitude'],
                                post['longitude'],
                                '',
                                'tweet',
                                '',
                                )
                        else:

                            raise StandardError('No twitpic url')
                else:
                    acted = False
                    logging.error('Processing post %s: unknown action: %s'
                                  % (str(post), post['action']))
                if acted:
                    did_something = True

            if not did_something:
                logging.error('Post %s not handled by any accounts!'
                              % (str(post)))
                if settings.value('ShowInfos', None) == '2':
                    self.dbus_handler.info(
                        'Khweeteur: Post %s not handled by any accounts!'
                        % (str(post)))

            logging.debug("post processed, deleting %s" % item)
            try:
                os.remove(item)
            except OSError, exception:
                logging.error("remove (processed) file %s: %s"
                              % (item, str(exception)))
        except twitter.TwitterError, err:

            if err.message == 'Status is a duplicate.':
                logging.error('Do_posts (remove): %s' % (err.message, ))
                os.remove(item)
            elif 'ID' in err.message:
                logging.error('Do_posts (remove): %s' % (err.message, ))
                os.remove(item)
            else:
                logging.error('Do_posts : %s' % (err.message, ))
            if settings.contains('ShowInfos'):
                if settings.value('ShowInfos') == '2':
                    self.dbus_handler.info('Khweeteur: Error occured while posting: '
                             + err.message)

        except Exception, err:

            # Emitting the error will block the other tweet post
            # raise #can t post, we will keep the file to do it later

            logging.exception('Do_posts : %s' % str(err))
            if settings.contains('ShowInfos'):
                if settings.value('ShowInfos') == '2':
                    self.dbus_handler.info('Khweeteur: Error occured while posting: '
                             + str(err))

    @Slot()
    def update(self):
        try:
            self.do_posts()
            self.retrieve_all()
        except Exception, e:
            logging.exception('update(): %s', (str(e),))

    def retrieve(self, account, thing):
        api = account.api
        me_user_id = account.me_user
        if not me_user_id:
            logging.debug("Account %s not authenticated. Not fetching '%s'"
                          % (str(account), thing))
            return

        try:
            t = KhweeteurRefreshWorker(account, thing)
            t.error.connect(self.dbus_handler.info)
            t.finished.connect(self.athread_end)
            t.terminated.connect(self.athread_end)
            t.new_tweets.connect(self.dbus_handler.new_tweets)
            t.start()
            self.threads[t] = (thing + ' on ' + account.uuid)
        except Exception, err:
            logging.error(
                'Creating worker for account %s, job %s: %s'
                % (str(account), thing, str(err)))

    def clean(self, settings=None):
        """
        Remove old tweets in cache according to history prefs.
        """
        if settings is None:
            settings = settings_db()

        # By default, flush status updates 3 days after their last
        # modification.
        keep_time = int(settings.value('tweetHistoryKeepTime',
                                       3 * 24 * 60 * 60))

        # But, always keep at least 60 status updates.
        keep_count = int(settings.value('tweetHistory', 60))

        now = time.time()

        nb_searches = settings.beginReadArray('searches')
        searches = []
        for index in range(nb_searches):
            settings.setArrayIndex(index)
            searches.append(settings.value('terms'))
        settings.endArray()

        search_directories = ['Search:' + s.replace('/', '_')
                              for s in searches]

        for folder in os.listdir(self.cache_path):
            path = os.path.join(self.cache_path, folder)

            if not os.path.isdir(path):
                continue

            if (folder.startswith('Search:')
                and folder not in search_directories):
                logging.debug(
                    "Search %s removed (not in %s).  Removing cached tweets."
                    % (folder, str(searches)))
                try:
                    shutil.rmtree(path)
                except OSError, exception:
                    logging.error("Removing stale directory %s: %s"
                                  % (path, str(exception)))
                try:
                    os.remove(path + '-data-cache')
                except OSError, exception:
                    logging.error("Removing %s: %s"
                                  % (path + '-data-cache', str(exception)))
                continue

            uids = os.listdir(path)

            uid_count = len(uids)
            if uid_count <= keep_count:
                # The total number of files does not exceed the keep
                # threshhold.  There is definately nothing to do.
                continue

            statuses = []
            filenames = {}
            kept = 0
            for uid in uids:
                try:
                    with open(os.path.join (path, uid), 'rb') as pkl_file:
                        status = pickle.load(pkl_file)
                except StandardError, err:
                    logging.exception('Error loading %s: %s' % (uid, str(err)))
                    continue

                if status.created_at_in_seconds > now - keep_time:
                    kept += 1
                    continue

                filenames[status] = uid
                statuses.append(status)

            statuses.sort(key=lambda status: status.created_at_in_seconds,
                          reverse=True)

            to_keep = keep_count - kept
            if to_keep < 0:
                to_keep = 0

            logging.debug(("%s: %d files less than %d days old; %d more; "
                           + "will keep %d of the latter (keep count: %d)")
                          % (path, kept, keep_time / (24 * 60 * 60),
                             len(statuses), to_keep, keep_count))

            if to_keep > 0:
                statuses = statuses[to_keep:]

            logging.debug("%s: expunging %d files" % (path, len(statuses)))
            for status in statuses:
                filename = os.path.join (path, filenames[status])
                try:
                    os.remove(filename)
                except StandardError, err:
                    logging.debug('remove(%s): %s'
                                  % (filename, str(err)))


    def retrieve_all(self):
        logging.debug('Start update')
        settings = settings_db()

        showInfos = False
        if settings.contains('ShowInfos'):
            if settings.value('ShowInfos') == '2':
                showInfos = True

        if len(self.threads)>0:
            for t, job in self.threads.items():
                # Check whether the threads are really running.  the
                # finished and terminate signals appear rather
                # unreliable.
                logging.debug("%s: %s: isRunning: %d; isFinished: %d"
                              % (str(t), job, t.isRunning(), t.isFinished()))
                if t.isFinished():
                    self.thread_exited(t)

            if len(self.threads)>0:
                # Update in progress.
                logging.info('Update in progress (%s jobs still running: %s)'
                             % (len (self.threads), str(self.threads.values())))
                return            

        self.clean(settings)

        for account in accounts():
            for feed in account.feeds():
                if feed == "Near":
                    if not self.geoloc_source:
                        self.geolocStart()
                    if self.geoloc_coordinates:
                        feed = ('Near:%s:%s'
                                % (str(self.geoloc_coordinates[0]),
                                   str(self.geoloc_coordinates[1])))
                    else:
                        continue

                self.retrieve(account, feed)

    @Slot()
    def kill_thread(self):
        for thread, tid in self.threads.items():
            if not thread.isFinished():
                logging.debug("Terminating thread %s: %s"
                              % (str(thread), tid))
                thread.terminate()

    def thread_exited(self, thread):
        logging.debug("Job %s finished; Jobs still running: %s"
                      % (self.threads[thread],
                         str ([v for k, v in self.threads.items()
                               if k != thread])))
        try:
            del self.threads[thread]
        except ValueError, exception:
            logging.debug("Unregistered thread %s called athread_end (%s)!"
                          % (str(thread), str (exception)))

        if len(self.threads) == 0:
            self.dbus_handler.refresh_ended()
            logging.debug('Finished update')

    @Slot()
    def athread_end(self):
        if self.sender() not in self.threads:
            logging.debug("athread_end called by %s, but not in self.threads"
                          % (self.sender()))
            return

        return self.thread_exited(self.sender())

if __name__ == '__main__':
    install_excepthook(__version__)

    def usage(exit_status=2):
        print ('usage: %s start|stop|restart|startfromprefs|debug'
               % sys.argv[0])
        sys.exit(2)

    if len(sys.argv) != 2:
        usage()

    detach_process = None
    if sys.argv[1] == 'debug':
        detach_process = False
        sys.argv[1] = 'start' 

    if 'startfromprefs' == sys.argv[1]:
        if settings_db().value('useDaemon') == '2':
            sys.argv[1] = 'start'
        else:
            sys.exit(0)

    if sys.argv[1] in ['start', 'stop', 'restart']:
        khweeteurdaemon = KhweeteurDaemon()

        if detach_process is None:
            khweeteurdaemon.stdin_path = "/dev/null"
            khweeteurdaemon.stdout_path = os.path.expanduser(
                '~/.khweeteur.log.out')
            khweeteurdaemon.stderr_path = os.path.expanduser(
                '~/.khweeteur.log.err')

        khweeteurdaemon.pidfile_path = os.path.expanduser('~/.khweeteur.pid')
        khweeteurdaemon.pidfile_timeout = 3

        khweeteurdaemon.detach_process = detach_process

        runner = DaemonRunner(khweeteurdaemon)
        try:
            runner.do_action()
        except LockTimeout:
            print "Failed to acquire lock."
            sys.exit(1)
    else:
        logging.error('Unknown command')
        print "Unknown command: '%s'" % (sys.argv[1],)
        usage(2)
