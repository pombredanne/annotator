#!/usr/bin/env python3
'''
annotator: annotate CASICS database entries and perform other annotation tasks
'''
__version__ = '1.0.0'
__author__  = 'Michael Hucka <mhucka@caltech.edu>'
__email__   = 'mhucka@caltech.edu'
__license__ = 'GPLv3'

from   datetime import datetime
import operator
import os
import plac
import subprocess
import sys
import tempfile
from   time import sleep
from   timeit import default_timer as timer

sys.path.append('../database')
sys.path.append('../common')

from casicsdb import *
from credentials import *


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

_CASICS_DB_NAME = 'lcsh-db'
'''The name of the CASICS database.'''

_CASICS_KEYRING = "org.casics.casics"
'''The name of the keyring entry for LoCTerms client users.'''


# Main body.
# .............................................................................

repos_collection = None
lcsh_collection = None

def main(annotate=False, dev_mode=False, find=None,
         list_repos=False, list_terms=False, summarize=False,
         casics_user=None, casics_pswd=None, casics_host=None, casics_port=None,
         locterms_user=None, locterms_pswd=None, locterms_host=None, locterms_port=None,
         nokeyring=False, *repos):

    # Dealing with negated variables is confusing, so turn it around.
    keyring = not nokeyring

    # Check arguments.
    if dev_mode:
        annotate = True
    if annotate and (list_repos or list_terms or summarize or find):
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
        (s_user, s_pswd, s_host, s_port) = get_keyring_credentials(_CASICS_KEYRING)
        if s_user != casics_user or s_pswd != casics_pswd or \
           s_host != casics_host or s_port != casics_port:
            save_keyring_credentials(_CASICS_KEYRING, casics_user, casics_pswd,
                                     casics_host, casics_port)
        (s_user, s_pswd, s_host, s_port) = get_keyring_credentials(_LOCTERMS_KEYRING)
        if s_user != locterms_user or s_pswd != locterms_pswd or \
           s_host != locterms_host or s_port != locterms_port:
            save_keyring_credentials(_LOCTERMS_KEYRING, locterms_user,
                                     locterms_pswd, locterms_host, locterms_port)
    if not (annotate or list_repos or list_terms or summarize or find):
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
                # Save the return value so we can exit with that value.
                proc = subprocess.Popen(cmd)
                proc.wait()
            finally:
                proc.terminate()
        sys.exit(retval)

    # We will continue here if annotate is true.
    global repos_collection
    global lcsh_collection
    repos_collection = get_repos(casics_user, casics_pswd, casics_host, casics_port)
    lcsh_collection = get_lcsh(locterms_user, locterms_pswd, locterms_host, locterms_port)

    if list_repos or list_terms or summarize:
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
        if summarize:
            print_stats(annotated)
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


def print_stats(annotated):
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
    casicsdb = CasicsDB(host=host, port=port, user=user, password=password)
    github_db = casicsdb.open('github')
    repos_collection = github_db.repos
    return repos_collection


def get_lcsh(user, password, host, port):
    db = MongoClient('mongodb://{}:{}@{}:{}/lcsh-db?authSource=admin'.format(
        user, password, host, port), serverSelectionTimeoutMS=_CONN_TIMEOUT)
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


# Plac annotations for main function arguments
# .............................................................................
# Argument annotations are: (help, kind, abbrev, type, choices, metavar)
# Plac automatically adds a -h argument for help, so no need to do it here.

main.__annotations__ = dict(
    annotate      = ('start the annotation web interface',                   'flag',   'a'),
    dev_mode      = ('start web interface in development mode (implies -a)', 'flag',   'A'),
    find          = ('find repos annotated with the given term',             'option', 'f'),
    list_repos    = ('list annotated repos',                                 'flag',   'l'),
    list_terms    = ('list LCSH terms used in repo annotations so far',      'flag',   't'),
    summarize     = ('print annotation summary statistics',                  'flag',   'm'),
    casics_user   = ('CASICS database user name',                            'option', 'u'),
    casics_pswd   = ('CASICS database user password',                        'option', 'p'),
    casics_host   = ('CASICS database server host',                          'option', 's'),
    casics_port   = ('CASICS database connection port number',               'option', 'o'),
    locterms_user = ('LoCTerms database user name',                          'option', 'U'),
    locterms_pswd = ('LoCTerms database user password',                      'option', 'P'),
    locterms_host = ('LoCTerms database server host',                        'option', 'S'),
    locterms_port = ('LoCTerms database connection port number',             'option', 'O'),
    nokeyring     = ('do not use a keyring',                                 'flag',   'X'),
    repos         = 'one or more repository identifiers or names',
)


# Entry point
# .............................................................................

plac.call(main)


# For Emacs users
# ......................................................................
# Local Variables:
# mode: python
# python-indent-offset: 4
# End:
