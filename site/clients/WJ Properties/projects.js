// WJ Properties project registry.
// Octrees + panoramas are served from the Cloudflare R2 bucket 'enable-pointclouds'
// (NOT the git repo). The viewer loads <folder>/cloud.js and derives the stations
// folder as <folder>/../../stations, so the two 7-St-Dennis projects share the
// "wj/7stdennis" prefix to keep using the same stations manifest.
window.PROJECTS = {
  "southGarageSlab": {
    name: "7 St Dennis - South Garage Slab",
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/7stdennis/pointclouds/southGarageSlab"
  },
  "ph8": {
    name: "PH8",
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/140carlton/pointclouds/ph8"
  },
  "carltonwall": {
    name: "140 Carlton - Wall",
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/140carltonwall/pointclouds/wall"
  },
  "unit2304": {
    name: "140 Carlton - Unit 2304",
    // Own R2 prefix (wj/140carlton2304) so its 3 panoramas stay isolated from the
    // other 140 Carlton projects' stations.
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/140carlton2304/pointclouds/unit2304"
  },
  "lounge": {
    name: "7 St Dennis - Lounge",
    // Own R2 prefix (separate from wj/7stdennis) so its 4 panoramas stay isolated
    // from the garage slab's shared stations.
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/7stdennislounge/pointclouds/lounge"
  },
  "sprinkler_room": {
    name: "2560 Kingston Rd - Sprinkler Room",
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/2560kingston/pointclouds/sprinkler-room"
  },
  "7_st_dennis_scan": {
    name: "7 St Dennis - South Garage Slab (original scan)",
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/7stdennis/pointclouds/7_st_dennis_scan"
  },
  "partialslab": {
    name: "7 St Dennis - Partial Slab Replacement",
    // Own R2 prefix (wj/7stdennispartialslab) so its 14 panoramas stay isolated from the
    // other 7-St-Dennis projects' shared stations manifest.
    folder: "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/7stdennispartialslab/pointclouds/scan"
  },
  // viewer-publish begin emw-2026-024 (published from Tekla; the publish tool rewrites this block)
  "emw-2026-024": {
    "name": "7 St Dennis Temp Generator Area",
    "folder": "https://pub-3f436f87578a4223ae3a342484363f71.r2.dev/wj/emw-2026-024/pointclouds/scan"
  },
  // viewer-publish end emw-2026-024
};
