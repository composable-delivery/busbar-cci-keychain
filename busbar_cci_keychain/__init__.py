"""CumulusCI keychain for running cci under the Busbar nono sandbox.

The nono proxy injects the org's real Salesforce session into every outbound
request, so cci must NOT hold — or fabricate — a credential. This keychain hands
cci an :class:`OrgConfig` carrying only the real instance URL (from
``SF_INSTANCE_URL``) and a placeholder token that the proxy replaces. The org's
real identity (``org_id`` / ``user_id``) is resolved LIVE from the org's OpenID
``userinfo`` endpoint through the proxy, so cci's org-info loading queries the
true org id instead of ``None`` (the bug behind the ``KeyError 'OrganizationType'``
seen with a hand-rolled ``CUMULUSCI_ORG_*`` blob).

Point cci at it:

    CUMULUSCI_KEYCHAIN_CLASS=busbar_cci_keychain.BusbarKeychain

Optional env:
    SF_INSTANCE_URL   (required) the org this run targets.
    BUSBAR_CCI_ORG    the org name to register (default ``dev``).
"""

import os

import requests

from cumulusci.core.config.org_config import OrgConfig
from cumulusci.core.exceptions import CumulusCIException
from cumulusci.core.keychain.base_project_keychain import BaseProjectKeychain

# Non-empty placeholder Bearer value. The proxy strips the client's Authorization
# header and injects the real session, so this value never reaches Salesforce and
# no real or fabricated credential lives in the sandboxed process.
_PLACEHOLDER_TOKEN = "busbar-proxy-injected"


class BusbarOrgConfig(OrgConfig):
    """An OrgConfig whose authentication is owned by the Busbar proxy.

    Never refreshes or mints a token. Resolves the real identity (and hence the
    real ``org_id``) from the org's ``userinfo`` endpoint through the proxy, then
    loads org + user info via the standard queries (which the proxy authenticates).
    """

    def refresh_oauth_token(self, keychain, connected_app=None, is_sandbox=False):
        # No token refresh: the proxy owns auth. Resolve identity first so
        # `_load_orginfo`'s Organization.get(org_id) uses the real org id.
        self._load_userinfo()
        self._ensure_identity()
        self._load_orginfo()

    def _ensure_identity(self):
        """Populate ``config['id']`` (the Salesforce identity URL) so cci can
        derive the real ``org_id`` and ``user_id``."""
        if self.config.get("id"):
            return
        userinfo = self.config.get("userinfo") or {}
        sub = userinfo.get("sub")
        if not sub:
            # `_load_userinfo` should have populated it; fall back to a direct call.
            resp = requests.get(
                self.instance_url.rstrip("/") + "/services/oauth2/userinfo",
                headers={"Authorization": "Bearer " + self.access_token},
                timeout=30,
            )
            resp.raise_for_status()
            userinfo = resp.json()
            self.config["userinfo"] = userinfo
            sub = userinfo.get("sub")
        if not sub:
            raise CumulusCIException(
                "BusbarKeychain could not resolve the org identity: the userinfo "
                "response carried no `sub`. Is the request reaching the org through "
                "the Busbar proxy (HTTPS_PROXY + CA bundle set)?"
            )
        # `sub` is the Salesforce identity URL `.../id/<orgId>/<userId>`; cci
        # derives org_id (`[-2]`) and user_id (`[-1]`) from it.
        self.config["id"] = sub
        self.config.setdefault("username", userinfo.get("preferred_username"))


class BusbarKeychain(BaseProjectKeychain):
    """Provides a proxy-backed :class:`BusbarOrgConfig` for any requested org.

    No stored orgs, no ``CUMULUSCI_ORG_*`` JSON, no fabricated ids: the org is
    synthesized from ``SF_INSTANCE_URL`` and resolved live through the proxy.
    """

    def _load_orgs(self):
        # Register the default org name when the target is known, so `cci org
        # list` and default resolution work; construction stays cheap otherwise.
        if os.environ.get("SF_INSTANCE_URL"):
            name = os.environ.get("BUSBAR_CCI_ORG", "dev")
            self.orgs[name] = self._make_org(name)

    def _get_org(self, name):
        org = self.orgs.get(name)
        return org if org is not None else self._make_org(name)

    def _make_org(self, name):
        instance_url = os.environ.get("SF_INSTANCE_URL", "").rstrip("/")
        if not instance_url:
            raise CumulusCIException(
                "BusbarKeychain requires SF_INSTANCE_URL to identify the target org."
            )
        return BusbarOrgConfig(
            {"instance_url": instance_url, "access_token": _PLACEHOLDER_TOKEN},
            name,
            keychain=self,
        )
