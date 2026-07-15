"""CumulusCI org registration for locked-down Dev Hub packaging accounts.

The `devhub-pbo-pkg-create` / `devhub-pbo-pkg-promote` service accounts (see
scripts/bootstrap/setup-devhub-ci-users.mjs) are deliberately least-privilege:
they can create/promote Package2Version records and nothing else. In
particular they cannot read the standard `Organization` sObject -- confirmed
empirically against the real Dev Hub: an `<objectPermissions>` grant for it
deploys without error but is silently dropped on save, on any license.
`Organization` simply isn't a permission-set-grantable object.

Stock CumulusCI's `cci org import` hardcodes `SfdxOrgConfig`, whose
`_load_orginfo()` unconditionally calls `Organization.get(org_id)` -- both
from `populate_expiration_date()` (at import) and `refresh_oauth_token()`
(on every later token refresh). That 404s for these accounts even though
their session is otherwise perfectly valid, because `create_package_version`
/ `promote_package_version` never read org_type/is_sandbox/namespace.

`PackagingOrgConfig` skips that query. `PackagingKeychain` is required
alongside it: `EncryptedFileProjectKeychain._construct_config` hardcodes
`SfdxOrgConfig` for any persisted org with `config["sfdx"]` set, discarding
whatever subclass actually saved it -- every separate `cci` process
reconstructs the org fresh from the on-disk keychain file, not from memory.
Without this keychain override, the class chosen by `ImportPackagingOrg`
would only survive for the single process that ran it, not later `cci flow
run --org packaging` invocations in the same CI job.
"""

from cumulusci.core.config.sfdx_org_config import SfdxOrgConfig
from cumulusci.core.keychain import EncryptedFileProjectKeychain
from cumulusci.core.tasks import BaseTask
from cumulusci.utils.options import CCIOptions, Field

# Marks a persisted org config as belonging to PackagingOrgConfig, so
# PackagingKeychain can pick the right class back up on a later `cci`
# invocation that only sees the org's serialized config dict.
_MARKER = "busbar_packaging"


class PackagingOrgConfig(SfdxOrgConfig):
    """SfdxOrgConfig for an org whose user cannot read `Organization`.

    Every persistent (non-scratch) Dev Hub is production and non-trial by
    construction, so synthesizing those fields loses no real information
    for this org's actual use (package version creation/promotion).
    """

    def _load_orginfo(self):
        self._org_sobject = {
            "OrganizationType": "Production",
            "IsSandbox": False,
            "InstanceName": None,
            "NamespacePrefix": None,
            "TrialExpirationDate": None,
        }
        self.config.update(
            {
                "org_type": self._org_sobject["OrganizationType"],
                "is_sandbox": self._org_sobject["IsSandbox"],
                "instance_name": self._org_sobject["InstanceName"],
                "namespace": self._org_sobject["NamespacePrefix"],
            }
        )


class PackagingKeychain(EncryptedFileProjectKeychain):
    """EncryptedFileProjectKeychain that reconstructs `_MARKER`-tagged orgs
    as PackagingOrgConfig. Every other org (scratch, plain sfdx, etc.) is
    unaffected -- this only intercepts the one marker this package sets.

    Cannot delegate to super()._construct_config(): it re-derives
    config_class from config["scratch"]/config["sfdx"] itself and discards
    whatever class is passed in, so a config dict that (deliberately) also
    carries "sfdx": True would otherwise be clobbered back to
    SfdxOrgConfig. The scratch/sfdx branches below are copied from
    EncryptedFileProjectKeychain._construct_config so non-packaging orgs
    keep exactly stock behavior, including honoring a scratch_org_factory
    override from that module.
    """

    def _construct_config(self, config_class, args):
        from cumulusci.core.keychain import encrypted_file_project_keychain as _efpk

        config = args[0]
        if config.get(_MARKER):
            config_class = PackagingOrgConfig
        elif config.get("scratch"):
            config_class = _efpk.scratch_org_factory
        elif config.get("sfdx"):
            config_class = SfdxOrgConfig
        return config_class(*args)


class ImportPackagingOrg(BaseTask):
    """Registers a persistent SFDX-authenticated org via PackagingOrgConfig,
    skipping the Organization read that `cci org import` requires.

    Use this (not `cci org import`) for any org authenticated as one of the
    `devhub-pbo-pkg-*` service accounts. Requires `cumulusci.keychain` (in
    cumulusci.yml or CUMULUSCI_KEYCHAIN_CLASS) to be set to
    `busbar_cci_keychain.packaging.PackagingKeychain` -- otherwise later
    `cci` invocations against this org will hit the same Organization 404.
    """

    task_docs = (
        "Registers a persistent SFDX-authenticated org via PackagingOrgConfig, "
        "bypassing the Organization sObject read that `cci org import` requires."
    )
    salesforce_task = False

    class Options(CCIOptions):
        username_or_alias: str = Field(
            ...,
            description="SFDX alias or username already authenticated via `sf org login`.",
        )
        org_name: str = Field(
            ..., description="Name to register this org under in CumulusCI's keychain."
        )
        config_file: str = Field(
            "config/project-scratch-def.json",
            description=(
                "Scratch-org-definition JSON to read org-shape settings "
                "(edition/features/language/etc.) from. create_package_version "
                "requires org_config.config_file even when the target org is a "
                "persistent Dev Hub, not an actual scratch org -- it never "
                "creates a scratch from this file, only reads shape settings."
            ),
        )

    parsed_options: Options

    def _run_task(self):
        org_config = PackagingOrgConfig(
            {
                "username": self.parsed_options.username_or_alias,
                "sfdx": True,
                _MARKER: True,
                "config_file": self.parsed_options.config_file,
            },
            self.parsed_options.org_name,
            self.project_config.keychain,
            global_org=False,
        )
        org_config.config["expires"] = "Persistent"
        org_config.save()
        self.logger.info(
            f"Imported packaging org '{self.parsed_options.org_name}' "
            f"(username/alias: {self.parsed_options.username_or_alias})"
        )
