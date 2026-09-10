# MODEL-001 | Red Fox Racer

## Standalone Task

Create a procedural Three.js model of the Red Fox Racer for Animal Racing. The fox should have a rounded body, orange-red fur, a white chest, and a fluffy white-tipped tail, in a quadrupedal racing pose.

This task can be completed independently. The constraints below provide the appearance, dimensions, and runtime information needed; no other project files are required.

## Modeling Scope

- Fully represent the Red Fox Racer's body, identifying features, and necessary structural hierarchy.
- Use separate, named nodes for parts that can move, change color, be shown or hidden, or serve as attachment points.
- A small number of presentation components bound to this model may be included, but do not include other standalone models or an entire scene in this file.
- Do not reproduce the exact appearance of copyrighted characters. Any homage should retain only abstract gameplay roles and generic character-type features.

## Modeling Facts

The following specification is the direct basis for this model. Preserve constraints concerning identity, shape, dimensions, pivots, orientation, materials, attachment points, collision proxies, animation, and bound presentation. Gameplay values are provided only to clarify the model's structure; do not implement gameplay systems in `model.js`.

> # 2. Assets
>
> ## 2.1 Scope and Standard Record Format
>
> - This module describes only the specifications and identities of objects that can be generated, rendered, collided with, or interacted with. Animation and VFX are not separate modules: character actions belong to the corresponding animal asset, and environment or ability effects belong to the owning asset's `bound_presentation`.
> - Collision proxies and collision layers belong to this model module; the host system handles spatial boundaries and gameplay responses to triggers.
> - Use this fixed field order for every asset; do not begin with a dimensions table:
>   `name/ID -> what_it_is -> species_or_item_type -> game_role -> appearance -> dimensions_and_pivot -> runtime_contract -> bound_presentation`.
> - All models: fewer than 80k triangles per model, textures at most 1024 x 1024, and a low-poly toy-like style with rounded shapes, broad surfaces, saturated colors, and no realistic PBR detail. Pivot: bottom center. Forward axis: +Z. Units: meters.
> - Persistent fields: no object-level persistent fields except carrot inventory, unlocks, and attributes. Race objects are transient and discarded on refresh.
>
> (Relevant asset entry)
>
> ### AST-FOX-01 Red Fox
> - **what_it_is**: A cartoon red fox with orange-red fur, a white chest, and a white tail tip. Its quadrupedal body has the most balanced proportions. It is the default player-controlled racer and represents stable, balanced performance.
> - **species_or_item_type**: Animal / red fox; racer.
> - **game_role**: Default player vehicle and carrier of the signature "High-Speed Dash" ability; also an AI candidate.
> - **appearance**: Orange-red fur #E8703A, chest/belly and tail tip #F5F1E6, ear tips #3A2A22; large triangular ears and a large fluffy tail; large rounded eyes and a small black nose. The overall shape is rounded, without sharp corners, and toy-like. Highly saturated warm colors ensure readability against grass.
> - **dimensions_and_pivot**: Bounding dimensions: 0.92 m wide x 0.7 m high x 1.5 m long. Pivot: bottom center beneath the hips. Forward axis: +Z, toward the nose. Stands on four legs; the torso pitches forward and backward by +/-6 degrees while running.
> - **runtime_contract**:
>   - Collider: capsule with radius 0.42 m and height 0.72 m, plus four wheel sampling points for surface detection, with 0.9 m front-to-rear spacing and left/right offsets of 0.32 m. Collision layer: `layer=racer`.
>   - Interaction layers: soft pushing between racer and racer, hard boundaries between racer and rail, and trigger interactions with pad, oil, and checkpoint.
>   - States: `COUNTDOWN / DRIVE / ABILITY_ACTIVE / SLIP / SHIELD / CHARGE / FINISHED / RECOVERING`.
>   - Lifecycle: spawn at the starting position during PREP -> remain throughout the race -> stop running and celebrate after FINISHED -> destroy when leaving the scene. Transient; position is not persisted.
>   - Persistent fields: none. Abilities and attributes are read from table 0.6, not stored on the entity.
> - **bound_presentation**:
>   - Rig/animation: `idle` (seated crouch) / `countdown` (crouch and stretch) / `run` (quadrupedal running loop, leg swing linked to speed from 0 to 13 m/s) / `steerL` / `steerR` (torso yaw +/-14 degrees) / `dash` (forward lean with a level tail) / `slip` (all four feet skid and shake) / `charge` (antlers not applicable) / `finish` (jump and wag the tail).
>   - Ability VFX, bound to this animal and triggered by the ability activation event: `VFX-FOX-DASH` consists of close-fitting orange speed lines near the ground, not enlarged to fill the screen (R-03), plus a tail-flame trail. Lifetime: the 4.0 s ability duration. Low-quality setting: speed lines only, with the trail disabled.
>   - Bound audio tags: running, slipping, and ability activation.

## Deliverables

- Deliver only one file, `model.js`. Do not depend on GLB, GLTF, FBX, OBJ, images, or remote resources.
- Use a Three.js ES module. `model.js` must export `createModel(THREE, options = {})`, returning an independent `THREE.Group`.
- The root Group must have a stable name. All moving parts must have meaningful English node names and be exposed through `group.userData.parts`.
- Also export `updateModel(group, deltaTime, state = {})` for animation. Provide a no-op implementation even if the model has no animation.
- The file must not create a renderer, scene, camera, lights, game loop, DOM elements, or global event listeners, and must not modify a supplied scene.
- Generate geometry procedurally in code. The default pivot is at the bottom center, the forward direction is +Z, and units are meters.

## Cel-Shaded Art Guidelines

- Use an animated, cel-shaded 3D style: a clear silhouette, simplified large color areas, and rounded or deliberately low-poly forms.
- Use 2 to 4 shading bands. Prefer `MeshToonMaterial`; small amounts of emissive materials, transparent planes, or vertex colors may supplement it.
- Add optional outlines to major contours using inverted-hull outlines or outline markers compatible with post-processing. Outlines must not cause obvious visual artifacts.
- Do not use photographic textures, realistic grime, complex PBR noise, or microsurface detail. Colors and shapes must remain readable at medium and long distances.
- Default materials must support an external key light; the model must not add its own lights. Manage sorting and depth writing for transparent materials.

## Code and Acceptance Contract

- `createModel` must support repeated calls without sharing mutable state between instances.
- `options` must support at least `scale`, `palette`, `outline`, and `quality`.
- `group.userData.bounds` must provide `{ width, height, depth }`; `group.userData.modelId` must match the task ID.
- Configure `castShadow` and `receiveShadow` appropriately for all meshes; disable shadow casting for decorative planes where needed.
- Provide `disposeModel(group)` to release geometry, materials, and textures created by this instance.
- The model must be immediately visible when instantiated in an empty Three.js scene. There must be no console errors, NaN values, or network requests.
- Acceptance covers only this task's target model. Do not implement gameplay rules, AI, HUD, audio, or a complete scene.

## File Location

Generate `model.js` in this task's directory.
