#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2010 Benoît HERVIER
# Licenced under GPLv3

#import sip
#sip.setapi('QString', 2)
#sip.setapi('QVariant', 2)

import sys, time
from PySide.QtCore import QSettings
import atexit, os
from signal import SIGTERM 

import logging

from retriever import KhweeteurRefreshWorker

import gobject
gobject.threads_init()

__version__ = '0.2.0'

import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import threading

import twitter
from urllib import urlretrieve
import urllib2
import pickle
import glob

try:
    from PIL import Image
except:
    import Image

from PySide.QtCore import QSettings

from threading import Thread

import logging
import os
import os.path
import dbus
import dbus.service


#A hook to catch errors
def install_excepthook(version):
    '''Install an excepthook called at each unexcepted error'''
    __version__ = version

    def my_excepthook(exctype, value, tb):
        '''Method which replace the native excepthook'''
        #traceback give us all the errors information message like the method, file line ... everything like
        #we have in the python interpreter
        import traceback
        trace_s = ''.join(traceback.format_exception(exctype, value, tb))
        print 'Except hook called : %s' % (trace_s)
        formatted_text = "%s Version %s\nTrace : %s" % ('Khweeteur', __version__, trace_s)
        logging.error(formatted_text)
        
    sys.excepthook = my_excepthook

class Daemon:
    """
    A generic daemon class.
    
    Usage: subclass the Daemon class and override the run() method
    """
    def __init__(self, pidfile, stdin='/dev/null', stdout='/dev/null', stderr='/dev/null'):
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.pidfile = pidfile

    def daemonize(self):
        """
        do the UNIX double-fork magic, see Stevens' "Advanced 
        Programming in the UNIX Environment" for details (ISBN 0201563177)
        http://www.erlenstar.demon.co.uk/unix/faq_2.html#SEC16
        """
        try: 
            pid = os.fork() 
            if pid > 0:
                # exit first parent
                sys.exit(0) 
        except OSError, e: 
            sys.stderr.write("fork #1 failed: %d (%s)\n" % (e.errno, e.strerror))
            sys.exit(1)
    
        # decouple from parent environment
        os.chdir("/") 
        os.setsid() 
        os.umask(0) 
    
        # do second fork
        try: 
            pid = os.fork() 
            if pid > 0:
                # exit from second parent
                sys.exit(0) 
        except OSError, e: 
            sys.stderr.write("fork #2 failed: %d (%s)\n" % (e.errno, e.strerror))
            sys.exit(1) 
    
        # redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        si = file(self.stdin, 'r')
        so = file(self.stdout, 'a+')
        se = file(self.stderr, 'a+', 0)
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())
    
        # write pidfile
        atexit.register(self.delpid)
        pid = str(os.getpid())
        file(self.pidfile,'w+').write("%s\n" % pid)
    
    def delpid(self):
        os.remove(self.pidfile)

    def start(self):
        """
        Start the daemon
        """
        # Check for a pidfile to see if the daemon already runs
        try:
            pf = file(self.pidfile,'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None
    
        if pid:
            message = "pidfile %s already exist. Daemon already running?\n"
            sys.stderr.write(message % self.pidfile)
            sys.exit(1)
        
        # Start the daemon
        self.daemonize()
        self.run()

    def stop(self):
        """
        Stop the daemon
        """
        # Get the pid from the pidfile
        try:
            pf = file(self.pidfile,'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None
    
        if not pid:
            message = "pidfile %s does not exist. Daemon not running?\n"
            sys.stderr.write(message % self.pidfile)
            return # not an error in a restart

        # Try killing the daemon process    
        try:
            while 1:
                os.kill(pid, SIGTERM)
                time.sleep(0.1)
        except OSError, err:
            err = str(err)
            if err.find("No such process") > 0:
                if os.path.exists(self.pidfile):
                    os.remove(self.pidfile)
            else:
                print str(err)
                sys.exit(1)

    def restart(self):
        """
        Restart the daemon
        """
        self.stop()
        self.start()

    def run(self):
        """
        You should override this method when you subclass Daemon. It will be called after the process has been
        daemonized by start() or restart().
        """

class DaemonDBus(dbus.service.Object):
    '''DBus Object handle dbus callback'''
    def __init__(self,parent):
        bus_name = dbus.service.BusName('net.khertan.khweeteur_daemon', bus=dbus.SessionBus())
        dbus.service.Object.__init__(self, bus_name, '/net/khertan/khweeteur_daemon')
        self.parent = parent
        
    @dbus.service.method(dbus_interface='net.khertan.khweeteur_daemon')
    def retrieve(self):
        '''Callback called to active the window and reset counter'''
        self.parent.retrieve()
        return True
                    
class KhweeteurDaemon(Daemon):
    def run(self):        
        logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename='/home/user/.khweeteur.log',
                    filemode='w')

        self.dbus_object = DaemonDBus(self)
        
        self.threads = [] #Here to avoid gc
        self.cache_path = os.path.join(os.path.expanduser("~"),\
                                 '.khweeteur','cache')
        if not os.path.exists(self.cache_path):
            os.makedirs(self.cache_path)
            
        loop = gobject.MainLoop()
        gobject.timeout_add_seconds(1,self.retrieve)
        logging.debug('Timer added')        
        loop.run()

    def retrieve(self):
        settings = QSettings("Khertan Software", "Khweeteur")
        logging.debug('Setting loaded')
        try:
            #Re read the settings
            settings.sync()
            logging.debug('Setting synced')
            
            #Cleaning old thread reference for keep for gc
            for thread in self.threads:
                if not thread.isAlive():
                    self.threads.remove(thread)
                    logging.error('Removed a thread')
            
            #Verify the default interval
            if not settings.contains('refresh_interval'):
                refresh_interval = 600
            else:
                refresh_interval = int(settings.value('refresh_interval'))*60
                if refresh_interval<600:
                    refresh_interval = 600
            logging.debug('refresh interval loaded')

            #Remove old tweets in cache according to history prefs
            try:
                keep = int(settings.value('tweetHistory'))
            except:
                keep = 60

            
            for root, folders, files in os.walk(self.cache_path):
                for folder in folders:
                    uids = glob.glob(folder)
                    statuses = []
                    for uid in uids:
                        uid = os.path.basename(uid)
                        try:
                            pkl_file = open(os.path.join(folder, uid), 'rb')
                            status = pickle.load(pkl_file)
                            pkl_file.close()
                            statuses.append(status)
                        except:
                            pass
                    statuses.sort()
                    statuses.reverse()
                    for status in statuses[keep:]:
                        try:
                            os.remove(os.path.join(folder,status.id))
                        except:
                            logging.debug('Cannot remove : %s' % str(status.id))          

            nb_accounts = settings.beginReadArray('accounts')
            logging.info('Found %s account' % (str(nb_accounts),))
            for index in range(nb_accounts):
                settings.setArrayIndex(index)

                #Worker
                try:                               
                    self.threads.append(KhweeteurRefreshWorker(\
                                settings.value('base_url'),
                                settings.value('consumer_key'),
                                settings.value('consumer_secret'),
                                settings.value('token_key'),
                                settings.value('token_secret'),
                                'HomeTimeline'
                                ))
                except Exception, err:
                    logging.error('Timeline : %s' % str(err))

                try:                                                   
                    self.threads.append(KhweeteurRefreshWorker(\
                                settings.value('base_url'),
                                settings.value('consumer_key'),
                                settings.value('consumer_secret'),
                                settings.value('token_key'),
                                settings.value('token_secret'),
                                'Mentions'
                                ))
                except Exception, err:
                    logging.error('Mentions : %s' % str(err))

                try:                               
                    self.threads.append(KhweeteurRefreshWorker(\
                                settings.value('base_url'),
                                settings.value('consumer_key'),
                                settings.value('consumer_secret'),
                                settings.value('token_key'),
                                settings.value('token_secret'),
                                'DMs'
                                ))
                except Exception, err:
                    logging.error('DMs : %s' % str(err))
                try:                               
                    for idx,thread in enumerate(self.threads):
                        logging.debug('Try to run Thread : %s' % str(thread))
                        try:
                            self.threads[idx].start()
                        except RuntimeError,e:
                            logging.debug('Attempt to start a thread already running')
                except:
                    logging.error('Running Thread error')

            settings.endArray()
                        
            logging.debug('Finished loop')          
                            
        except StandardError,err:
            logging.exception(str(err))
            logging.debug(str(err))

        gobject.timeout_add_seconds(refresh_interval,self.retrieve)
        return False

                         
if __name__ == "__main__":
    install_excepthook(__version__)
    daemon = KhweeteurDaemon('/tmp/khweeteur.pid')
    if len(sys.argv) == 2:
            if 'start' == sys.argv[1]:
                    daemon.start()
            elif 'stop' == sys.argv[1]:
                    daemon.stop()
            elif 'restart' == sys.argv[1]:
                    daemon.restart()
            else:
                    print "Unknown command"
                    sys.exit(2)
            sys.exit(0)
    else:
            print "usage: %s start|stop|restart" % sys.argv[0]
            sys.exit(2)