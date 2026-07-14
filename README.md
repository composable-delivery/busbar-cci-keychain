# busbar-cci-keychain

CumulusCI keychain and org-config extensions for running `cci` without static, long-lived Salesforce credentials.

```
pip install busbar-cci-keychain
```

## `busbar_cci_keychain.BusbarKeychain`

For orgs reached through the [Busbar](https://github.com/composable-delivery/busbar-broker-sf) OIDC trust exchange under the `nono` sandbox: the sandboxing proxy injects the org's real session at request time, so `cci` never holds or fabricates a credential. Point `cci` at it with:

```
CUMULUSCI_KEYCHAIN_CLASS=busbar_cci_keychain.BusbarKeychain
```

Requires `SF_INSTANCE_URL` for the target org; org identity is resolved live from the org's `/services/oauth2/userinfo` endpoint through the proxy.

## `busbar_cci_keychain.packaging`

For CumulusCI-driven 2GP package pipelines that authenticate a locked-down, least-privilege Salesforce Dev Hub service account (one that can create/promote package versions but nothing else — notably, cannot read the standard `Organization` sObject, which is not a permission-set-grantable object on any Salesforce license). Stock CumulusCI's `cci org import` unconditionally reads `Organization` for any persistent org it registers.

```yaml
# cumulusci.yml
cumulusci:
  keychain: busbar_cci_keychain.packaging.PackagingKeychain

tasks:
  import_packaging_org:
    class_path: busbar_cci_keychain.packaging.ImportPackagingOrg
```

```
cci task run import_packaging_org -o username_or_alias <sfdx-alias> -o org_name packaging
cci flow run release_2gp_beta --org packaging
```

`PackagingKeychain` is a strict superset of the stock `EncryptedFileProjectKeychain` — it only intercepts orgs registered through `import_packaging_org`; every other org (scratch, plain sfdx, etc.) behaves exactly as stock CumulusCI.
