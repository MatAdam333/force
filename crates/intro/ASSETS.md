# Assets

Everything in the film comes from these sources, all CC0 (public domain): free to use, modify and
redistribute, commercially too. No attribution is required; they're credited here anyway. The motion
itself is the intro's own procedural motion (`motion/`).

| Asset | Used for | Source | Licence |
|---|---|---|---|
| Universal Base Characters (Standard): `Superhero_Male_FullBody`, `Hair_SimpleParted`, eyebrows | the man (slimmed to an athletic build; the suit is generated from the body) | Quaternius, <https://quaternius.com/packs/universalbasecharacters.html> | CC0 1.0 |
| Universal Animation Library (Standard): `Walk_Formal_Loop`, `Sitting_Enter`, `Sitting_Idle_Loop` | relaxed finger poses | Quaternius, <https://quaternius.com/packs/universalanimationlibrary.html> | CC0 1.0 |
| `mid_century_lounge_chair` (glTF, 2k textures) | the chair (reshaped: wider, a little shallower and lower) | Poly Haven, <https://polyhaven.com/a/mid_century_lounge_chair> | CC0 1.0 |
| `classic_laptop` (glTF, 2k textures) | the laptop (its screen blanked to black) | Poly Haven, <https://polyhaven.com/a/classic_laptop> | CC0 1.0 |
| `modern_arm_chair_01` (glTF, 2k textures) | the earlier armchair, optional (`CHAIR` in `scene.py`) | Poly Haven, <https://polyhaven.com/a/modern_arm_chair_01> | CC0 1.0 |

The splash's wordmark is set in **Archivo Black** (Copyright 2017 The Archivo Black Project Authors,
<https://github.com/Omnibus-Type/ArchivoBlack>), licensed under the **SIL Open Font License 1.1**
(`assets/ArchivoBlack-OFL.txt`; the font itself is in `tools/force/`). The binary embeds no font, only the word
`FORCE` rendered from it into a mask (`assets/force.mask`, made by `tools/force/bake.py`).

The source files are not in this repository, only the rendered film (`assets/intro.film`). To rebuild
it, put the downloads under `$FORCE_INTRO_ASSETS` (default `~/force-intro-assets`):

```
ubc/Universal Base Characters[Standard]/...      itch.io: quaternius/universal-base-characters
ual/Universal Animation Library[Standard]/...    itch.io: quaternius/universal-animation-library
mid_century_lounge_chair/mid_century_lounge_chair_2k.gltf + .bin + textures/   api.polyhaven.com/files/mid_century_lounge_chair
classic_laptop/classic_laptop_2k.gltf + .bin + textures/                       api.polyhaven.com/files/classic_laptop
armchair/modern_arm_chair_01_2k.gltf + .bin + textures/   (optional)           api.polyhaven.com/files/modern_arm_chair_01
```

Blender 4.5 LTS renders it (`blender/README.md`); the portable Linux build needs no install.
