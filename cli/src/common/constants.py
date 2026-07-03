"""Fixed identifiers shared across the app - nothing here should change at runtime."""

APP_NAME = "EasyUniVPN"

# Name of the openconnect-saml profile EasyUniVPN creates and manages. Internal
# only - the user never sees or chooses this.
PROFILE_NAME = "UniVPN"

VPN_SERVER = "univpn.uni-graz.at"
OPENCONNECT_APP = "openconnect-saml"

# Environment variable openconnect-saml reads to find its own config file.
# Set by common.openconnect_config.configure_openconnect_env() before every
# call into the library, so it always uses EasyUniVPN's app-data location
# instead of its own default.
OPENCONNECT_CONFIG_ENV = "OPENCONNECT_SAML_CONFIG"
