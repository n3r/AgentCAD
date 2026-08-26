# PRD-031c — Marketplace community layer

- **Status:** pending
- **Phase:** v6 — community (the third slice of PRD-031's split)
- **Created:** 2026-08-25
- **Origin:** founder direction from the Aug-2026 UX study ("a normal
  marketplace of items — images, 3D preview, comments, ratings, remix,
  save to libraries") + founder idea #1e
- **Depends on:** PRD-031b (open publishing, identity, moderation —
  hard) · PRD-031a (catalog — completed) · PRD-007 (customizer —
  completed) — soft: PRD-011 (libraries — completed), PRD-005
  (accounts — completed)
- **Related:** PRD-031, PRD-029 (skills distribution), PRD-036

## Problem & motivation

PRD-031a shipped the shelf (seeded read-only catalog, add-to-library);
PRD-031b opens publishing with verification tiers and moderation. What
neither covers is what makes MakerWorld and Printables *places* rather
than directories: the social loop — galleries that sell the part,
comments that answer "does it actually print?", ratings that rank
honestly, remixing that turns consumers into contributors, and personal
collections that make returning worthwhile. The founder's example is
the north star: publish a brushless motor so anyone can produce or
assemble it — which only works if others can see it properly, ask
questions under it, rate it after building it, and fork it into their
own variant with credit preserved. The UX study validated the surface
grammar (card grid, detail with gallery + parameter table, comment
thread, remix action); the review fixed two honesty rules this PRD
inherits as invariants: never fabricate engagement (no default star
ratings), and remixes must be real forks with real provenance — never
a relabel.

## Users & jobs

- **Browser/maker:** judge a part in seconds (images, 3D preview, the
  parameter table with proven ranges), read whether it survived other
  people's printers, save it to a collection.
- **Publisher:** present work properly (gallery, description, specs),
  learn from comments, see honest adoption numbers.
- **Remixer:** fork with one action, keep attribution automatically,
  publish the variant with its lineage visible.
- **Agent:** read reviews/ratings as structured signals when
  recommending parts; draft listing copy and answer comment questions
  *as the publisher's delegate, always labeled as an agent* (PRD-031's
  disclosure rule).
- **Moderator (PRD-031b role):** the same report/act pipeline covers
  comments and galleries, not just listings.

## Goals

- G1. Listings gain media: an image gallery (publisher-uploaded renders
  and photos) and the existing kernel-free 3D preview (031a's mesh
  read), with the parameter table remaining the differentiator.
- G2. Comment threads per listing with publisher/agent labels, and
  ratings that only exist when a signed-in human leaves one — shown
  with counts and distribution, never seeded or defaulted.
- G3. Remix: a server-side fork of the listing's source into the
  user's own project with a recorded lineage (`remixed_from:
  publisher/listing@version`) that survives into any re-publication;
  listing pages show their remix family both directions.
- G4. Collections: named personal shelves over saved listings and
  installed packages (011's layer), shareable read-only.
- G5. All engagement surfaces respect the split's safety inversion:
  nothing here executes marketplace code on a consumer's machine —
  remix places *source* in the remixer's project where it is theirs,
  exactly as consequential as installing a package (the publish gate
  is a correctness gate; PRD-006 remains the boundary), and the UI
  says so at the remix moment.

## Non-goals

- Points/rewards economies, boosts, paid placement — the roadmap
  non-goal (farming lessons) stands; monetization is 031b's economy
  scope if it ever lands.
- Direct messaging, forums, follower graphs — comments-under-things
  only.
- Client-side execution of anything (unchanged inversion).
- Print-profile hosting per printer model — later, with PRD-037's
  profile story.

## Experience

The catalog detail page grows up: gallery left (arrows + thumbnails),
3D preview toggle, description and specs, the parameter table with
gate-proven ranges, install/customize (existing), and — new — Remix
and Save-to-collection actions; below, ratings summary (average,
count, histogram) and the comment thread. Commenting/rating require
sign-in (031b identity); the publisher's replies are badged, an
agent's replies are badged as agent. Remix confirms with the honest
sentence about whose kernel the code will run in, then lands the
source in the chosen project with the lineage chip visible in the
part's properties (and in PRD-034's tree); "Publish remix" pre-fills
attribution. Collections live in the user's library rail; a
collection link renders read-only for the signed-out.

## Functional requirements

- FR1. Media: listings accept N images (publisher-owned, moderated
  under 031b's pipeline, size/type-capped, content-addressed like
  package trees); the 3D preview stays the 031a kernel-free mesh path.
- FR2. Comments: threaded one level, plain text, per-listing;
  publisher and agent badges from the identity layer; edit/delete by
  author; report → 031b moderation. Rate limits ride the shared
  token-bucket primitive.
- FR3. Ratings: one per human account per listing version-line,
  editable; aggregate = average + count + histogram; **no rating
  exists by default and commenting never implies one** (review
  invariant).
- FR4. Remix: `remix_listing {listing, project}` copies the listing's
  source tree into the project (a normal editable part, not a locked
  package ref), records lineage in the part's provenance header, and
  the listing's remix count increments; re-publication carries the
  lineage into the new listing (031b publish flow reads it).
- FR5. Lineage display: a listing shows "remixed from" upward and a
  count/list downward; broken upstream (deleted/yanked) renders
  honestly ("source listing no longer available") without breaking
  the local part.
- FR6. Collections: CRUD over saved listings + installed packages;
  a share link renders the collection read-only through the existing
  public-surface rules (name-free 404s and scope filters exactly as
  031a's anonymous catalog).
- FR7. Agent surface reads engagement as structure:
  `get_listing_social {listing}` → ratings aggregate + recent
  comments; agents never rate; agent comments carry the label
  server-side (not client courtesy).
- FR8. All new anonymous reads join the enumerated public surface
  with its equality test (the PRD-005a discipline); every addition
  gets its negation test.

## Agent surface

`get_listing_social` (read) · `remix_listing` (acts as the signed-in
principal; refused for anonymous) · publisher-delegate commenting via
the existing chat/tool identity with the mandatory agent label.
Structured errors (`listing_gone`, `rating_requires_human`,
`media_rejected {reason}`).

## Technical approach

Server-side state lives with 031b's marketplace service (comments,
ratings, media, lineage are storefront data, not package-format data —
the package tree digest stays untouched); the client app only renders.
Remix reuses the package materialization path (verified fetch) into a
project write, then stamps provenance (the `remove_package`-style
header discipline — additive fields only, never touching script
bytes... except remix *is* a source copy, so the header is written at
copy time, once). Public reads extend `routes_public`-class packs;
media through the same content-addressed cache verification as
packages.

## MVP & phasing

- **MVP (with 031b):** galleries, comments with badges + moderation
  hooks, honest ratings, save-to-collection; remix into a project
  with lineage; `get_listing_social`.
- **Phase 2:** remix-family browsing, collection share links,
  publish-remix attribution pre-fill, agent publisher-delegate
  replies.
- **Phase 3:** print-result reports ("built one" with printer/
  material), richer media (turntables from server renders), skills
  listings joining the same social surface (PRD-029's marketplace
  distribution).

## Acceptance criteria

- AC1. Browser: a signed-in user rates and comments on a listing;
  aggregate updates; signed-out sees both but can do neither; no
  listing anywhere shows a rating with zero raters.
- AC2. Remix lands editable source in a project with the lineage
  visible in part properties; publishing that part pre-fills
  attribution; the origin listing's remix count increments.
- AC3. Yanking the origin renders the descendant's lineage line
  honestly while the local part keeps building (negative test).
- AC4. An agent comment posted through the publisher's delegate path
  carries the agent badge server-side (API-level assertion, not CSS).
- AC5. The anonymous-surface equality test covers every new public
  route; each prefix has its negation test (the 005a discipline).
- AC6. Media uploads verify content-address on fetch and reject
  over-cap/wrong-type with structured errors. Full suite green.

## Risks & open questions

- **Moderation load** scales with exactly these features; MVP gates on
  031b's pipeline actually existing, and every surface ships with
  report + rate-limit from day one.
- **Rating integrity** (sockpuppets): one-per-human-per-line +
  031b verification tiers; publish the aggregation rule; revisit
  weighting only with evidence.
- **Remix vs package-pin philosophy:** remix copies source (editable,
  diverges) while `use_part` pins (locked, updatable) — the UI must
  teach the difference at the decision point, or users will fork when
  they meant to pin. Wording test in design.
- **Where galleries live for git-index packages** (no storefront
  backend): likely storefront-only feature; git indexes keep the
  parameter-table-and-preview experience. Decide in design.

## Competitive references

MakerWorld's social loop is the reference for engagement mechanics —
and for the failure mode we exclude (points economies driving farm
content; market_research.md, marketplaces deep dive). Printables
proves remix-with-attribution culture. Neither can show proven
parameter ranges, kernel-validated remixes, or CI-gradable lineage —
the engineering substrate underneath the social layer is the moat this
PRD builds on, not the social layer itself.
