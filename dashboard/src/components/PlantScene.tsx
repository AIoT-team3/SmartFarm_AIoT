import { useEffect, useRef, useState } from "react";
import { RotateCcw, Expand, Leaf } from "lucide-react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

function createLeaf(length: number, width: number, color: number) {
  const rows = 20;
  const columns = 8;
  const points: number[] = [];
  const indices: number[] = [];
  for (let row = 0; row <= rows; row++) {
    const t = row / rows;
    for (let column = 0; column <= columns; column++) {
      const side = (column / columns) * 2 - 1;
      const span = Math.pow(Math.sin(Math.PI * t), 0.8) * width;
      const serration = 1 + 0.035 * Math.sin(t * Math.PI * 18);
      points.push(
        side * span * serration,
        Math.sin(t * Math.PI) * 0.13 + side * side * 0.08 - t * t * 0.13,
        t * length,
      );
      if (row < rows && column < columns) {
        const i = row * (columns + 1) + column;
        indices.push(
          i,
          i + 1,
          i + columns + 1,
          i + 1,
          i + columns + 2,
          i + columns + 1,
        );
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(points, 3),
  );
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const leaf = new THREE.Group();
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color,
      roughness: 0.77,
      side: THREE.DoubleSide,
    }),
  );
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  leaf.add(mesh);
  const vein = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.005, 0),
    new THREE.Vector3(0, 0.105, length * 0.45),
    new THREE.Vector3(0, -0.12, length),
  ]);
  leaf.add(
    new THREE.Mesh(
      new THREE.TubeGeometry(vein, 16, 0.006, 4, false),
      new THREE.MeshStandardMaterial({ color: 0x78a85b, roughness: 0.9 }),
    ),
  );
  return leaf;
}

function createPlant() {
  const plant = new THREE.Group();
  const potMaterial = new THREE.MeshStandardMaterial({
    color: 0xb96c48,
    roughness: 0.94,
  });
  const profile = [
    new THREE.Vector2(0.43, 0),
    new THREE.Vector2(0.46, 0.06),
    new THREE.Vector2(0.66, 0.94),
    new THREE.Vector2(0.65, 1.02),
    new THREE.Vector2(0.59, 1.02),
    new THREE.Vector2(0.56, 0.87),
  ];
  const pot = new THREE.Mesh(new THREE.LatheGeometry(profile, 64), potMaterial);
  pot.castShadow = true;
  pot.receiveShadow = true;
  plant.add(pot);
  const rim = new THREE.Mesh(
    new THREE.TorusGeometry(0.625, 0.048, 12, 64),
    potMaterial,
  );
  rim.rotation.x = Math.PI / 2;
  rim.position.y = 1;
  rim.castShadow = true;
  plant.add(rim);
  const soil = new THREE.Mesh(
    new THREE.CylinderGeometry(0.574, 0.574, 0.035, 64),
    new THREE.MeshStandardMaterial({ color: 0x40392b, roughness: 1 }),
  );
  soil.position.y = 0.91;
  soil.receiveShadow = true;
  plant.add(soil);
  const stemCurve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(0, 0.9, 0),
    new THREE.Vector3(0.04, 1.8, 0),
    new THREE.Vector3(-0.02, 2.8, 0.04),
    new THREE.Vector3(0.03, 3.05, 0.02),
  ]);
  const stem = new THREE.Mesh(
    new THREE.TubeGeometry(stemCurve, 32, 0.021, 8, false),
    new THREE.MeshStandardMaterial({ color: 0x718940, roughness: 0.9 }),
  );
  stem.castShadow = true;
  plant.add(stem);
  const colors = [0x397544, 0x46894b, 0x559652, 0x397947];
  for (let level = 0; level < 8; level++) {
    const size = 0.74 - level * 0.061;
    for (let side = 0; side < 2; side++) {
      const leaf = createLeaf(
        size,
        size * 0.42,
        colors[(level + side) % colors.length],
      );
      leaf.position.set(Math.sin(level) * 0.03, 1.27 + level * 0.224, 0);
      leaf.rotation.y = level * 1.46 + side * Math.PI;
      leaf.rotation.x = -0.34 - level * 0.026;
      leaf.rotation.z = side ? -0.12 : 0.1;
      plant.add(leaf);
    }
  }
  for (let i = 0; i < 3; i++) {
    const leaf = createLeaf(0.28, 0.1, 0x78a85b);
    leaf.position.y = 2.93;
    leaf.rotation.set(-0.82, (i * Math.PI * 2) / 3, 0);
    plant.add(leaf);
  }
  return plant;
}

export function PlantScene({
  future = false,
  day = 1,
}: {
  future?: boolean;
  day?: number;
}) {
  const growthDay = Math.max(1, Math.min(28, Math.floor(day)));
  const host = useRef<HTMLDivElement>(null);
  const reset = useRef<() => void>(() => {});
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const element = host.current!;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch {
      setUnavailable(true);
      return;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(33, 1, 0.1, 100);
    camera.position.set(4, 3.4, 6.7);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 1.45, 0);
    controls.enableDamping = true;
    controls.enablePan = false;
    controls.minDistance = 4.5;
    controls.maxDistance = 11;
    controls.maxPolarAngle = Math.PI / 2.1;
    controls.saveState();
    reset.current = () => controls.reset();
    scene.add(new THREE.HemisphereLight(0xffffff, 0xa0b3a3, 2.5));
    const sun = new THREE.DirectionalLight(0xfff8df, 3.3);
    sun.position.set(-3, 6, 4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    sun.shadow.normalBias = 0.025;
    scene.add(sun);
    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(1.32, 1.32, 0.085, 80),
      new THREE.MeshStandardMaterial({ color: 0xdce5d6, roughness: 0.9 }),
    );
    base.position.y = -0.075;
    base.receiveShadow = true;
    base.castShadow = true;
    scene.add(base);
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(30, 30),
      new THREE.ShadowMaterial({ opacity: 0.11 }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.13;
    floor.receiveShadow = true;
    scene.add(floor);
    scene.add(createPlant());
    const resize = () => {
      const { width, height } = element.getBoundingClientRect();
      renderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    let frame = 0;
    const draw = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          const materials = Array.isArray(object.material)
            ? object.material
            : [object.material];
          materials.forEach((material) => material.dispose());
        }
      });
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);
  return (
    <div className={`plant-scene ${future ? "future" : ""}`}>
      <div
        ref={host}
        className="scene-canvas"
        role="img"
        aria-label="회전하고 확대할 수 있는 예시 식물 3D"
      />
      {unavailable && (
        <div className="scene-fallback">
          <Leaf size={68} />
          <p>이 브라우저에서 3D를 표시할 수 없어요.</p>
        </div>
      )}
      <div className="scene-controls">
        <button
          className="icon-button"
          onClick={() => reset.current()}
          title="3D 시점 초기화"
          aria-label="3D 시점 초기화"
        >
          <RotateCcw size={16} />
        </button>
        <button
          className="icon-button"
          title="식물 전체 화면"
          aria-label="식물 전체 화면"
          onClick={() => {
            if (document.fullscreenElement) void document.exitFullscreen();
            else
              void host.current?.parentElement
                ?.requestFullscreen()
                .catch(() => {});
          }}
        >
          <Expand size={16} />
        </button>
      </div>
      <p className="scene-help">드래그해서 회전 · 스크롤해서 확대</p>
      <div className="scene-caption">
        <span className="small-leaf">
          <Leaf size={13} />
        </span>
        <span>
          {growthDay}일차
          <span className="caption-sub">
            실측·예측 수치는 오른쪽에서 확인해요
          </span>
        </span>
      </div>
    </div>
  );
}
