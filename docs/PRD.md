# PRD: Citizen-Science Biodiversity Monitor (working title)

**Theme:** Sustainability & Social Impact | **Stack:** Google Cloud (Vertex AI, Gemini, BigQuery, Earth Engine, Cloud Storage, Cloud Run, Pub/Sub) | **Status:** Draft v2 (global; animals, birds, plants)

## 1. Summary

A global mobile app where people photograph animals, birds and plants, get an AI species identification, and earn XP weighted by how rare the species is in that region. Verified records flow to a dashboard for conservation agencies, parks, NGOs and researchers, who monitor distribution, detect invasive or declining species, and ask Gemini for trends and reports.

**Positioning:** a conservation decision layer fed by citizens. Species identification is already well served, so this product competes on verified, rarity-weighted incentives and on what organisations can do with the data.

## 2. Problem

- Biodiversity records are uneven: dense near cities and popular birding sites, sparse elsewhere.
- Conservation bodies lack a continuous, broad stream of verified sightings to catch invasions, range shifts and declines early.
- Consumer ID apps serve curiosity. Open platforms generate data but give organisations little decision tooling, and most avoid rewards for fear of data-quality damage.

## 3. Goals and non-goals

**Goals**

1. Collect verified, geotagged observations of animals, birds and plants worldwide.
2. Motivate contribution, especially in under-sampled areas, without degrading data quality.
3. Give organisations a trusted dashboard, alerts and AI-assisted analysis per region.
4. Protect sensitive species and their locations.

**Non-goals (v1)**

- Other taxa (fish, fungi, insects) and audio identification.
- Beating Merlin, Pl@ntNet or iNaturalist on raw ID accuracy.
- Replacing formal field surveys.

## 4. Users

| User | Need |
| --- | --- |
| Casual observer, student, hiker | Fast ID, progress, recognition |
| Dedicated naturalist | Lists, badges, credit for rare finds |
| Expert verifier | Efficient review queue for their region and taxon |
| Organisation analyst (agency, park, NGO, researcher) | Verified maps, alerts, trends, reports |

## 5. Competitive landscape

| Product | What it does | Gap vs. this PRD |
| --- | --- | --- |
| iNaturalist | Global photo ID, community verification, research-grade records | Staff avoid gamification over data-quality concerns; no organisation decision layer or rarity scoring |
| Seek | Instant ID with badges and challenges | Gamified, but observations stay out of the public dataset; no organisation link |
| Merlin Bird ID | Birds from photos, sound or guided questions; free | Birds only; learning tool, not an organisation dashboard |
| eBird | Checklist-based sighting records | Recording system, not an instant photo ID; birds only |
| Pl@ntNet | Plant ID with percentage confidence and crowd validation | Plants only; no rewards or decision layer |
| Project Noah | Global wildlife "spottings" and missions | Lighter verification; no analytics for organisations |

**Differentiators:** (1) animals, birds and plants in one network; (2) rarity computed per region from live data; (3) rewards gated by verification; (4) organisation workspaces with maps, invasive alerts and Gemini queries; (5) role-gated protection of sensitive locations.

**Honest risk:** these incumbents are strong and free. The pitch must lead with the organisation layer and the verified-reward loop, not the identification itself.

## 6. Design principle: rewards must not corrupt data

Rarity rewards invite fake or exaggerated sightings, which is why iNaturalist avoids them. Therefore:

- XP for rare species is provisional until verified; common species verify automatically.
- Repeat sightings of the same species at the same place and time earn little.
- Users gain or lose reputation by verification outcomes, and rare-bonus XP is capped per day.
- No rewards for records in restricted areas or involving disturbance of wildlife.

## 7. Functional requirements

### P0 (MVP)

**F1. Capture and identify.** Camera or gallery photo; top candidates with confidence; auto-recorded GPS, time and habitat tag; offline queue with sync; multilingual UI.

**F2. Rarity and gamification.** Rarity = f(regional sighting frequency in BigQuery, conservation status, endemism), recomputed on a schedule. XP, levels, badges, regional and global leaderboards. Rare-find rewards unlock only after verification.

**F3. Verification pipeline.** High confidence and common species: auto-accept. Low confidence or rare: expert queue, with a second reviewer for rare claims. Integrity checks: duplicate or stock images, EXIF/GPS/time consistency, out-of-range species.

**F4. Organisation dashboard.** Distribution maps by species, region and time; rare and invasive panels; trends; per-organisation regions of interest.

**F5. Sensitive-location protection.** Public views show generalised locations for threatened species; exact coordinates are role-gated and audit-logged.

### P1

- **F6. Invasive and new-record alerts** via Pub/Sub to subscribed organisations.
- **F7. Gemini analyst:** natural-language queries ("Which districts show declining raptors over two years?") and auto-generated periodic reports in the user's language.
- **F8. Environmental context:** Earth Engine layers (land cover, vegetation index, water, rainfall) overlaid on sightings.

### P2

- Predictive range-shift and decline models (Vertex AI).
- Optional size measurement with a reference object, for animals where body size informs population health.
- Insects, fungi and fish as extra taxa; bird sound ID.
- Disaster-impact monitoring (e.g., habitat change after floods or fires) using the same observation network.
- Open-data export with generalised sensitive records.

## 8. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Connectivity | Offline-first capture; regional model and species packs downloadable |
| Languages | Localised UI and species names; Gemini output in the user's language |
| Performance | ID within \~3 s online; dashboard queries under \~5 s |
| Privacy and compliance | Minimal personal data; location consent; account deletion; GDPR-style rights; child-safe defaults |
| Accuracy | Show uncertainty and alternatives; publish per-taxon accuracy |
| Safety | In-app guidance: keep distance from wildlife, avoid nests and dens, respect protected areas and private land |
| Data licensing | Confirm reuse terms for training, taxonomy and conservation-status sources before launch |

## 9. Architecture (Google Cloud)

| Layer | Service |
| --- | --- |
| Client | Flutter or React Native with offline queue |
| API, workers, multi-tenant organisation workspaces | Cloud Run |
| Images | Cloud Storage |
| Species ID | Vertex AI vision model, Gemini as fallback and explainer |
| Analytics, rarity, taxonomy and status tables | BigQuery |
| Environmental context | Earth Engine |
| Reports and natural-language queries | Gemini on Vertex AI |
| Alerts and workflows | Pub/Sub |

**Data flow:** capture → upload → ID → integrity checks → verification routing → BigQuery → rarity recompute → dashboard, alerts and reports.

## 10. Success metrics

- **Data:** verified observations per week; share of records from under-sampled regions; taxa and region coverage.
- **Quality:** expert-confirmed accuracy of auto-accepted records (target ≥ 90%); flag and fraud rate.
- **Engagement:** weekly active users; 30-day retention.
- **Impact:** organisations onboarded; alerts acted on; time from sighting to organisation alert.

## 11. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Fake rare sightings for XP | Verification-gated rewards, reputation, caps (section 6) |
| Poaching from exposed locations | Generalised public data, role-gated exact coordinates |
| Crowded market with strong free incumbents | Lead with organisation layer and verified-reward loop; integrate open data rather than rebuild it |
| Regional ID accuracy gaps | Candidate lists with confidence, expert review, retraining from verified data |
| Verifier supply at global scale | Recruit via universities, NGOs and parks; start with a few pilot regions |
| Unsafe or intrusive behaviour | Safety guidance, no rewards for disturbance or restricted areas |

## 12. Hackathon MVP scope

**Build:** F1–F5 (expert queue simulated), plus one of F6 or F7. **Demo narrative:** a user photographs a rare bird → provisional XP → expert verifies → record appears on the organisation map → analyst asks Gemini for a decline trend → an invasive alert fires. **Seed data:** label any simulated data clearly in the demo.

## 13. Open questions

1. Which pilot region and organisation partner first, and what data formats do they need?
2. Which sources define rarity and status (IUCN, national lists, GBIF frequencies), and what are their reuse terms?
3. Build a custom ID model, or fine-tune on open datasets and use pretrained models?
4. Is any reward beyond points appropriate (recognition, partner perks)?
