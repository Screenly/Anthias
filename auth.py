################################
# Authentication
################################

import ldap
from sys import stdout


auth_info = { 'credentials': {} }

def check_credentials_basic(username, password):
    if password and auth_info['credentials'].get(username) == password:
        return None
    else:
        return 'unknown user or invalid password'

# from https://gist.github.com/1288159
def check_credentials_ldap(username, password):
    """Verifies credentials for username and password.
    Returns None on success or a string describing the error on failure
    # Adapt to your needs
    """
    print 'check_credentials_ldap username='+str(username)
    stdout.flush()

    LDAP_SERVER = auth_info['ldapserver']
    # fully qualified AD user name
    LDAP_USERNAME = auth_info['ldapuserformat'] % username
    # your password
    LDAP_PASSWORD = password
    base_dn = auth_info['ldapbasedn']
    ldap_filter = auth_info['ldapfilterformat'] % username
    attrs = auth_info['ldapattributes']
    try:
        # build a client
        ldap_client = ldap.initialize(LDAP_SERVER)
        # perform a synchronous bind
        ldap_client.set_option(ldap.OPT_REFERRALS,0)
        ldap_client.simple_bind_s(LDAP_USERNAME, LDAP_PASSWORD)
    except ldap.INVALID_CREDENTIALS:
        ldap_client.unbind()
        print 'check_credentials_ldap username='+str(username)+' : '+'Wrong username ili password'
        stdout.flush()
        return 'Wrong username ili password'
    except ldap.SERVER_DOWN:
        print 'check_credentials_ldap username='+str(username)+' : '+'AD server not available'
        stdout.flush()
        return 'AD server not available'
    # all is well
    # get all user groups and store it in cerrypy session for future use
    values = ldap_client.search_s(base_dn, ldap.SCOPE_SUBTREE, ldap_filter, attrs)
    ldap_client.unbind()
    dept_ok = False
    for dn, entry in values:
        print 'check_credentials_ldap search result: dn: '+str(dn)+' entry: '+str(entry)
        if dn != None:
            dept_ok = True
    if not dept_ok:
        print 'check_credentials_ldap username='+str(username)+' : '+ auth_info['ldapfiltererrmessage']
        stdout.flush()
        return 'wrong department'
    print 'check_credentials_ldap username='+str(username)+' : '+'None'
    stdout.flush()
    return None

def auth_config(key, val):
    auth_info[key] = val

def auth_check_credentials(auth, username, password):
    if auth == 'ldap':
        return check_credentials_ldap(username, password)
    else:
        return check_credentials_basic(username, password)


