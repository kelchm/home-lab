# Kanidm person accounts

People sign in to Kanidm with accounts declared as `KanidmPersonAccount` resources beside [`person-kelchm.yaml`](../../kubernetes/apps/identity/kanidm/app/person-kelchm.yaml). Kaniop 0.21.0 creates the account; this procedure sets its first credentials without the reset link leaving your terminal.

## Why not kaniop's default

With the default `credentialBootstrapDelivery: event`, kaniop answers a person with no credentials by creating a reset token and publishing its link in a `TokenCreated` Kubernetes Event, and it publishes a new one each time the previous token expires (`credentialsTokenTtl`, default one hour) until credentials are set. Anything that can read Events in `identity` can read the link, including the `view`-bound MCP service accounts in `ai`. The events collector redacts the token before it reaches VictoriaLogs (see [Query-string redaction](logging.md#query-string-redaction)), but that is a backstop, not the delivery channel.

`credentialBootstrapDelivery: email` asks kaniop to have Kanidm email the link instead. No Kanidm mail sender is configured, so kaniop issues no token at all: it fails closed with a `MailSenderNotConfigured` warning Event and a `CredentialBootstrapDelivery=False` condition, and never falls back to an Event link (kaniop 0.21.0, `libs/person/src/reconcile.rs`). That leaves the reset token to you.

## Add a person

1. Add the manifest with email delivery and commit it:

   ```yaml
   apiVersion: kaniop.rs/v1beta1
   kind: KanidmPersonAccount
   metadata:
     name: <name>
   spec:
     kanidmRef:
       name: kanidm
     personAttributes:
       displayname: <Display Name>
       mail:
         - <address>
     # No mail sender is configured, so kaniop publishes no reset link.
     credentialBootstrapDelivery: email
   ```

2. After Flux applies it, confirm kaniop issued nothing: `kubectl -n identity events --for kanidmpersonaccount/<name>` shows `MailSenderNotConfigured` and no `TokenCreated`.

3. Create the reset token as `idm_admin`, whose password is in the `kanidm-admin-passwords` Secret (see the [cutover runbook](kanidm-kaniop-cutover.md#7-verify-the-operator-created-its-admin-secret)). The CLI image matches the server version in `kanidm.yaml`:

   ```sh
   docker run --rm -it kanidm/tools:1.11.2 sh -c '
     kanidm login -H https://auth.home.kelch.io -D idm_admin &&
     kanidm person credential create-reset-token <name> --ttl 3600 -H https://auth.home.kelch.io -D idm_admin'
   ```

   It prints the link and a QR code. Hand them to the person directly; the token is single-use and expires after `--ttl` seconds. If it expires unused, run step 3 again.

4. Once the person has set a password or passkey, kaniop marks the bootstrap complete: `kubectl -n identity get kanidmpersonaccount <name> -o jsonpath='{.status.credentialBootstrap.state}'` prints `complete`, and the warning condition clears.

The person's first visit to the link passes through `traefik-services`, whose access log records `/ui/reset?token=REDACTED`.

## Existing accounts

`kelchm` was bootstrapped through the Event link before this procedure existed and already has credentials, so kaniop issues nothing for it. To recover a person's lost credentials, create a reset token as in step 3.
