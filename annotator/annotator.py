#!/usr/bin/env python3
'''
annotator: annotate CASICS database entries and perform other annotation tasks
'''

from   datetime import datetime
import operator
import os
import plac
from   pymongo import MongoClient
import subprocess
import sys
import tempfile
from   time import sleep
from   timeit import default_timer as timer

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.casicsdb import *
from common.messages import *
from common.credentials import *


# Global constants.
# .............................................................................

_CONN_TIMEOUT = 5000
'''Time to wait for connection to databases, in milliseconds.'''

# LoCTerms database defaults.

_LOCTERMS_DEFAULT_HOST = 'localhost'
'''Default network host for LoCTerms server if no explicit host is given.'''

_LOCTERMS_DEFAULT_PORT = 27017
'''Default network port for LoCTerms server if no explicit port number is given.'''

_LOCTERMS_DB_NAME = 'lcsh-db'
'''The name of the LoCTerms database.'''

_LOCTERMS_KEYRING = "org.casics.locterms"
'''The name of the keyring entry for LoCTerms client users.'''

# CASICS database defaults.

_CASICS_DEFAULT_HOST = 'localhost'
'''Default network host for CASICS server if no explicit host is given.'''

_CASICS_DEFAULT_PORT = 27017
'''Default network port for CASICS server if no explicit port number is given.'''

_CASICS_DB_NAME = 'github'
'''The name of the CASICS database.'''

_CASICS_KEYRING = "org.casics.casics"
'''The name of the keyring entry for LoCTerms client users.'''


# Global variables.
# .............................................................................

repos_collection = None
lcsh_collection = None


# Main body.
# .............................................................................

# Plac automatically adds a -h argument for help, so no need to do it here.
@plac.annotations(
    annotate      = ('start the annotation web interface',               'flag',   'a'),
    dev_mode      = ('start interface in development mode (implies -a)', 'flag',   'A'),
    find          = ('find repos annotated with the given term',         'option', 'f'),
    list_repos    = ('list annotated repos',                             'flag',   'l'),
    list_terms    = ('list LCSH terms used in repo annotations so far',  'flag',   't'),
    casics_user   = ('CASICS database user name',                        'option', 'u'),
    casics_pswd   = ('CASICS database user password',                    'option', 'p'),
    casics_host   = ('CASICS database server host',                      'option', 's'),
    casics_port   = ('CASICS database connection port number',           'option', 'o'),
    locterms_user = ('LoCTerms database user name',                      'option', 'U'),
    locterms_pswd = ('LoCTerms database user password',                  'option', 'P'),
    locterms_host = ('LoCTerms database server host',                    'option', 'S'),
    locterms_port = ('LoCTerms database connection port number',         'option', 'O'),
    nokeyring     = ('do not use a keyring',                             'flag',   'X'),
)

def main(annotate=False, dev_mode=False, find=None,
         list_repos=False, list_terms=False, nokeyring=False,
         casics_user=None, casics_pswd=None, casics_host=None, casics_port=None,
         locterms_user=None, locterms_pswd=None, locterms_host=None, locterms_port=None,
         ):
    '''Annotate CASICS database entries and perform other annotation tasks.
Requires the CASICS database server to be running for query tasks; for
annotation tasks, also requires a LoCTerms server to be running.  Basic usage:

    annotator -a
        Start the annotation interface in a web browser.

    annotator -l
        Generate a list of annotated repositories.

    annotator -t
        Generate a list of LCSH terms used in all annotations so far.

By default, this uses the operating system's keyring/keychain functionality
to get the user name and password needed to access both the CASICS database
server and the LoCTerms database over the network.  If no such credentials
are found, it will query the user interactively for the user name and
password for each system separately (so, two sets), and then store them in
the keyring/keychain (unless the -X argument is given) so that it does not
have to ask again in the future.  It is also possible to supply user names
and passwords directly using command line arguments, but this is discouraged
because it is insecure on multiuser computer systems. (Other users could run
"ps" in the background and see your credentials).

Additional arguments can be used to specify the host and port on which the
CASICS database and LoCTerms database processes are listening.
'''
    # Dealing with negated variables is confusing, so turn it around.
    keyring = not nokeyring

    # Check arguments.
    if dev_mode:
        annotate = True
    if annotate and (list_repos or list_terms or find):
        raise SystemExit('Cannot combine --annotate with other actions.')
    if not (casics_user and casics_pswd and casics_host and casics_port):
        (casics_user, casics_pswd, casics_host, casics_port) = obtain_credentials(
            _CASICS_KEYRING, "CASICS", casics_user, casics_pswd, casics_host,
            casics_port, _CASICS_DEFAULT_HOST, _CASICS_DEFAULT_PORT)
    if not (locterms_user and locterms_pswd and locterms_host and locterms_port):
        (locterms_user, locterms_pswd, locterms_host, locterms_port) = obtain_credentials(
            _LOCTERMS_KEYRING, "LoCTerms", locterms_user, locterms_pswd,
            locterms_host, locterms_port, _LOCTERMS_DEFAULT_HOST, _LOCTERMS_DEFAULT_PORT)
    if keyring:
        # Save the credentials if they're different from what's saved.
        (s_user, s_pswd, s_host, s_port) = get_credentials(_CASICS_KEYRING)
        if s_user != casics_user or s_pswd != casics_pswd or \
           s_host != casics_host or s_port != casics_port:
            save_credentials(_CASICS_KEYRING, casics_user, casics_pswd,
                             casics_host, casics_port)
        (s_user, s_pswd, s_host, s_port) = get_credentials(_LOCTERMS_KEYRING)
        if s_user != locterms_user or s_pswd != locterms_pswd or \
           s_host != locterms_host or s_port != locterms_port:
            save_credentials(_LOCTERMS_KEYRING, locterms_user,
                             locterms_pswd, locterms_host, locterms_port)
    if not (annotate or list_repos or list_terms or find):
        raise SystemExit('No action specified. (Use -h for help.)')

    # Start node process with the browser-based user interface.  To
    # communicate the user credentials, we avoid the most egregiously insecure
    # methods like passing command line arguments, and instead use a temporary
    # file.  This pushes security issues to filesystem security, which is still
    # not excellent but is better than many alternatives.
    if annotate:
        retval = 0
        with tempfile.NamedTemporaryFile(delete=True) as tmpfile:
            proc = None
            try:
                write_config(tmpfile, "casics", casics_user, casics_pswd,
                             casics_host, casics_port)
                write_config(tmpfile, "locterms", locterms_user, locterms_pswd,
                             locterms_host, locterms_port)
                cmd = ['env', 'ANNOTATOR_CONFIG={}'.format(tmpfile.name)]
                if dev_mode:
                    cmd += ['nodemon', '--debug', '-e', 'js,hbs']
                else:
                    cmd += ['node', 'index.js']
                here = os.path.dirname(__file__)
                # Save the return value so we can exit with that value.
                proc = subprocess.Popen(cmd, cwd=here)
                proc.wait()
            finally:
                proc.terminate()
        sys.exit(retval)

    # We will continue here if annotate is true.
    global repos_collection
    global lcsh_collection
    repos_collection = get_repos(casics_user, casics_pswd, casics_host, casics_port)
    lcsh_collection = get_lcsh(locterms_user, locterms_pswd, locterms_host, locterms_port)

    if list_repos or list_terms:
        msg('Gathering list of annotated repositories ...')
        annotated = {}
        for entry in repos_collection.find(
                {'topics.lcsh': {'$ne': []}},
                {'_id': 1, 'owner': 1, 'name': 1, 'topics': 1}):
            annotated[entry['_id']] = {'owner': entry['owner'],
                                       'name' : entry['name'],
                                       '_id'  : entry['_id'],
                                       'terms': entry['topics']['lcsh']}
        msg('Done.')
        print_totals(annotated)
        if list_repos:
            print_annotated(annotated)
        if list_terms:
            print_terms(annotated)

    # This will not be true if one of the other actions is true.
    if find:
        msg('Searching for repos annotated with {} ...'.format(find))
        results = repos_collection.find(
            {'topics.lcsh': {'$in': [find]}},
            {'_id': 1, 'owner': 1, 'name': 1, 'topics': 1})
        msg('Found {} repos:'.format(results.count()))
        for entry in results:
            print_repo(entry, prefix='   ')

    sys.exit(0)


def print_totals(annotated):
    msg('Total annotated repositories found: {}'.format(len(annotated)))


def print_repo(entry, prefix=''):
    msg('{}{}: {} terms'.format(prefix, e_summary(entry), len(entry['topics']['lcsh'])))
    msg(terms_explained(entry['topics']['lcsh'], prefix + '   '))


def print_annotated(annotated):
    for id, entry in annotated.items():
        msg('-'*70)
        msg('{}: {} terms'.format(e_summary(entry), len(entry['terms'])))
        msg(terms_explained(entry['terms'], prefix='    '))


def print_terms(annotated):
    global lcsh_collection
    (num, repos) = max_annotations(annotated)
    msg('Most number of terms on any repo: {}'.format(num))
    msg('└─ Repo(s) in question (total: {}): {}'.format(
        len(repos), ', '.join([e_summary(repo) for repo in repos])))
    # (terms, count) = most_used_terms(annotated)
    # msg('Most number of times any term is used: {}'.format(count))
    # msg('└─ Term(s) used that number of times: {}'.format(', '.join(terms)))
    msg('Term usage statistics:')
    counts = term_stats(annotated)
    for term, count in sorted(counts.items(), key=operator.itemgetter(1),
                             reverse=True):
        lcsh_entry = lcsh_collection.find_one({'_id': term}, {'label': 1})
        msg('  {0:>3}: {1} = {2}'.format(count, term, lcsh_entry['label']))


def terms_explained(terms, prefix=''):
    return prefix + ('\n' + prefix).join(
        term + ': ' + term_label(term) for term in terms)


def term_label(term):
    global lcsh_collection
    lcsh_entry = lcsh_collection.find_one({'_id': term}, {'label': 1})
    return lcsh_entry['label']


def max_annotations(annotated):
    total = 0
    repos = []
    for id, entry in annotated.items():
        this_len = len(entry['terms'])
        if this_len > total:
            total = this_len
            repos = [entry]
        elif this_len == total:
            repos.append(entry)
    return (total, repos)


def most_used_terms(annotated):
    counts = term_stats(annotated)
    terms = []
    values = list(counts.values())
    keys   = list(counts.keys())
    max_value = max(values)
    for pos, value in enumerate(values):
        if value == max_value:
            terms.append(keys[pos])
    return (terms, max_value)


def term_stats(annotated):
    counts = {}
    for id, entry in annotated.items():
        for term in entry['terms']:
            if term in counts:
                counts[term] += 1
            else:
                counts[term] = 1
    return counts


def get_repos(user, password, host, port):
    db = MongoClient('mongodb://{}:{}@{}:{}/github?authSource=admin'
                     .format(user, password, host, port),
                     serverSelectionTimeoutMS=_CONN_TIMEOUT,
                     tz_aware=True, connect=True, socketKeepAlive=True)
    github_db = db[_CASICS_DB_NAME]
    repos_collection = github_db.repos
    return repos_collection


def get_lcsh(user, password, host, port):
    db = MongoClient('mongodb://{}:{}@{}:{}/lcsh-db?authSource=admin'
                     .format(user, password, host, port),
                     serverSelectionTimeoutMS=_CONN_TIMEOUT,
                     tz_aware=True, connect=True, socketKeepAlive=True)
    lcsh_db = db[_LOCTERMS_DB_NAME]
    lcsh_terms = lcsh_db.terms
    return lcsh_terms


# Misc. utilities.
# .............................................................................

def write_config(tmpfile, section_name, user, password, host, port):
    def write_string(string):
        tmpfile.write(bytes(string + '\n', 'UTF-8'))

    def write_value(key, value):
        tmpfile.write(bytes(key + '=' + value + '\n', 'UTF-8'))

    write_string('[' + section_name + ']')
    write_value('host', host)
    write_value('port', str(port))
    write_value('user', user)
    write_value('password', password)
    tmpfile.flush()


# For Emacs users
# ......................................................................
# Local Variables:
# mode: python
# python-indent-offset: 4
# End:
