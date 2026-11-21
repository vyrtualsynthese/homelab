# Every provider is in an EU member state; Switzerland and the rest of Europe outside the EU are out

Date: 2026-11-21
Decided by: operator
Status: active

## The decision

Every service this homelab depends on for the operator's data or the cluster's identity runs on a
**European provider headquartered in an EU member state, with the data stored and served inside the
EU**. The operator is building the whole solution on sovereign clouds.

European is not the test; EU is. Switzerland is out, and so are the United Kingdom and Norway,
however good the service or the price. A company headquartered outside the EU is out too, even when
it sells an EU region: the data stays under its home jurisdiction.

The rule covers every service that holds data or authority for this cluster: object storage, DNS,
mail, backup destinations, container registries, identity. A dependency with no EU alternative is
written into the table below as an exception, with the date and what would replace it — it is not
left out quietly.

The next backup copy is where the rule was first applied: the primary is OVHcloud in `GRA`, and the
second copy is proposed on Scaleway in Paris (homelab task #536, infra-main task #534) instead of
the Swiss option that was on the table.

## How a candidate is checked

The first line is the operator's rule. The three below are how this session reads "sovereign" in
practice, and the operator can correct them.

1. The company is headquartered in an EU member state and governed by EU law plus that state's law.
2. The data and its metadata sit in an EU region: no replication, support access or failover
   outside it.
3. No non-EU parent with effective control, and no contractual path to another jurisdiction — the
   US CLOUD Act is the case worth checking.
4. Between two qualifying providers, a sovereign qualification breaks the tie: OVHcloud and
   Scaleway both sell SecNumCloud-qualified services.

## Why

The data being protected is the operator's own and irreplaceable: photos and documents, application
state, the configuration a rebuild needs. The move away from Google Drive is about who holds that
data, so replacing it with another provider outside the EU would rebuild the same dependency under
a different name.

Switzerland is European but not in the EU. Its data-protection law is adequate by an EU decision,
which is a transfer mechanism, not sovereignty; the same is true of the United Kingdom. The
operator's line is the stricter one, so the stricter one is what is written down.

Cost is not what decides at this scale. The four restic repositories are ~11 GB today and settle
around 25 GB; on a per-GB EU provider that is cents, and on a flat-tier EU provider it is the price
of one coffee a month. The expensive part of this rule is elsewhere: the services that have no EU
equivalent yet.

## Where this lands today

| Dependency | Provider | EU member state | Standing |
|---|---|---|---|
| DNS, primary backup bucket, Terraform state | OVHcloud, France | yes | complies |
| Second backup copy (proposed) | Scaleway, France | yes | complies |
| Object storage, not chosen | Hetzner, Germany | yes | qualifies; the flat 1 TB tier costs more at this volume |
| Object storage, rejected | Exoscale, Switzerland | **no** | out under this rule |
| Object storage and DNS, being retired | AWS | no | out; the AWS to OVH migration is in flight |
| Git hosting, Pages, Actions | GitHub | no | **open** — no decision yet |
| Container images | Docker Hub, `quay.io`, `ghcr.io`, `registry.k8s.io` | no | **open** |
| Certificates | Let's Encrypt | no | **open** |
| Media metadata | Plex, The Movie Database, the indexers | no | **open** |

The four open rows are not small: the git host is where this repository lives, the registries are
where every image comes from, and the certificates are on the public routes. They are listed so the
gap is visible, not to imply they are solved.

## What it costs if it is wrong

- **Money, barely.** EU object storage runs €0.008–0.02 per GB-month against the cheaper US tiers;
  at 25 GB the difference is a few euros a year. The rule is not expensive in storage.
- **Work, in the open rows.** Leaving GitHub, the registries or Let's Encrypt is migration work and
  an outage risk, not a bill. If the rule is wrong, that work was unnecessary; if the rule is right
  and never applied there, the sovereignty claim is only true of the storage.
- **Fewer options, smaller companies.** The EU-only set is smaller and its providers are smaller
  than AWS. An outage is more likely, which is why the answer is a second copy on a *different* EU
  provider rather than a fallback outside the EU.
