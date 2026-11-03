# Research scope of method 0.6

Method 0.5 used a weighted monotone Sugeno-equivalent score as a binary candidate gate. On TUB1 all 49 geometrically admissible interior proposals exceeded the 0.70 fuzzy threshold (minimum 0.784918); verticality/planarity/height-persistence memberships were saturated for most candidates. On the additional single building, five fuzzy-rejected patches were also removed by downstream geometric fragment filtering. The final meshes were therefore identical with and without that gate.

Method 0.6 separates robust geometric operations from experimental evidence and finite-candidate relational selection. The goal is to expose which evidence and neighbouring relations change the decision, including abstention/unknown evidence when appropriate. The implementation is not a claim that adding fuzzy complexity improves reconstruction.

The geometry still assumes a supported main interior level with predominantly orthogonal walls; additional facade and roof observations are reconstructed separately and combined conservatively. General multi-storey space reconstruction, curved-room boundaries, door/window semantics and recovery of the manually interpreted earlier room layout are not solved by this release. No complete-building watertight guarantee is made.

Potential future work is explicitly outside current claims: independent supervised membership/weight calibration, verified visibility/free-space evidence from scanner trajectories, alternative line/arc geometry hypotheses, and joint parameter optimization of a whole building graph. No missing scan region is to be called free space without observational support.

Relevant primary work includes Bassier and Vergauwen (2020), DOI 10.3390/rs12111800; Xiong et al. (2023), DOI 10.1016/j.autcon.2023.105097; Jarząbek-Rychard and Maas (2023), DOI 10.1016/j.autcon.2023.105002; and Malihi and Bosché (2024), DOI 10.5194/isprs-annals-X-4-2024-223-2024. The new profiles require their own validation and are not proven better by these citations.
