# Homepage

[Homepage](https://gethomepage.dev) serves the operator start page at `https://home.kelch.io`. It sits on `gateway-admin` behind the shared `ops-suite` Kanidm client, so only `ops-admins` can reach it. It is also the landing URL of the `ops-suite` and `arr-suite` tiles in Kanidm's app portal.

## Where tiles come from

- **In-cluster apps with an HTTPRoute** declare their own tile with `gethomepage.dev/*` annotations on that route. Homepage discovers every route annotated `gethomepage.dev/enabled: "true"`, takes the link from the route's hostname, and shows pod status from `app.kubernetes.io/name=<route name>` unless `gethomepage.dev/pod-selector` overrides it.
- **Off-cluster systems and in-cluster apps without a route** live in [`config/services.yaml`](homepage/app/config/services.yaml).
- **Group order, header widgets, and bookmarks** live in the other files under [`config/`](homepage/app/config/). The ConfigMap is directory-mounted, so edits take effect on the next page load once the kubelet syncs the volume.

Tiles merge by group name and sort by `weight`. The groups are `Media`, `Observability`, `Infrastructure`, and `Apps`.

## Adding a tile

1. Annotate the app's route with `enabled`, `group`, `name`, `icon`, `description`, and `weight`. Icons use [dashboard-icons](https://github.com/homarr-labs/dashboard-icons) names (`sonarr.svg`) or `mdi-*`.
2. For a service widget, add `widget.type`, `widget.url` (the in-cluster Service URL), and any credential as a `{{HOMEPAGE_VAR_<NAME>}}` placeholder. Put the value in [`secret.sops.yaml`](homepage/app/secret.sops.yaml). In app-template values, escape the placeholder from Helm's `tpl` as `'{{ "{{HOMEPAGE_VAR_<NAME>}}" }}'`. Charts that do not template annotations, such as Seerr's, take it verbatim.
3. Allow the widget's traffic: an egress rule in [`networkpolicy.yaml`](homepage/app/networkpolicy.yaml) and, if the target has an ingress policy, a matching rule there. Use an HTTP rule limited to the widget's read path when the target API is unauthenticated (qBittorrent, Longhorn manager, Alertmanager).

Annotation keys cannot contain brackets, so write list indices as `widget.mappings.0.label` rather than `mappings[0]`.

## Credentials

`homepage-secret` holds copies of the Sonarr, Radarr, Lidarr, Prowlarr, Bazarr, SABnzbd, Seerr, and qBittorrent API keys. Rotating one of those keys in its app means updating this Secret too (Reloader then restarts Homepage); the Sonarr, Radarr, and Lidarr keys are also in `media/arr-api-keys.sops.yaml`. qBittorrent's pod-CIDR authentication bypass does not apply to Homepage: its HTTP-restricted policy routes the connection through Cilium's L7 proxy, whose upstream source is outside that CIDR, so Homepage uses the API key.
