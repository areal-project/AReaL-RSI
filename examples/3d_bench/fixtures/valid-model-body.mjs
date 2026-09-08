export function createModel(THREE) {
  const root = new THREE.Group();
  root.name = 'model-001-fixture';
  const nodes = {};
  const { styleToon, outlined, register, createStaticActions, finalize } = createModelTools(THREE, nodes);
  const pivot = register('fixturePivot', new THREE.Group());
  root.add(pivot);
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(0.5, 12, 8), styleToon('coral'));
  mesh.position.y = 0.5;
  outlined(pivot, mesh, 0.012, 'fixtureMesh');
  return finalize(root, {
    contractVersion: 2,
    asset: { id: 'model-001-fixture', displayName: 'MODEL-001 fixture', category: 'prop', dimensions: [1, 1, 1], groundY: 0, frontAxis: '+Z', pivot: 'bottom-center' },
    render: { style: 'toon-3-step', castShadow: true, receiveShadow: true, outline: { required: true, screenWidthPx: [1, 2] } },
    requiredNodes: ['fixturePivot', 'fixtureMesh'], states: null,
    actions: createStaticActions('idle'), collision: null, lod: [], audio: { actions: {} },
    persistence: { enabled: false, fields: [] },
  });
}
