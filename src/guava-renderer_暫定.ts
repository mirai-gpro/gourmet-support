import * as THREE from 'three';

export class GuavaRenderer {
  private container: HTMLElement;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer;
  private material: THREE.ShaderMaterial | null = null;
  private geometry: THREE.BufferGeometry | null = null;
  private points: THREE.Points | null = null;
  private jawOpenAmount = 0;

  constructor(container: HTMLElement) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x000000);
    
    const pixelRatio = Math.min(window.devicePixelRatio, 2);
    this.renderer = new THREE.WebGLRenderer({ 
      alpha: false, 
      antialias: false,
      powerPreference: "high-performance"
    });
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(this.renderer.domElement);

    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 100);
    this.camera.position.set(0, 0, 2.2); 
    this.camera.lookAt(0, -0.1, 0);

    window.addEventListener('resize', this.onResize.bind(this));
    this.animate();
  }

  public async loadAssets(url: string) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const buffer = await response.arrayBuffer();
      this.setupGaussianShader();
      this.setupGeometryFromPLY(buffer);
      const fallback = document.getElementById('avatarFallback');
      if (fallback) fallback.style.display = 'none';
    } catch (e) {
      console.error('Failed to load assets:', e);
    }
  }

  private setupGaussianShader() {
    this.material = new THREE.ShaderMaterial({
      vertexShader: `
        attribute vec3 color; 
        attribute vec3 scale;
        attribute float opacity;
        uniform float jawOpen;
        varying vec3 vColor;
        varying float vAlpha;
        
        void main() {
          vec3 pos = position;
          float mouthCenterY = -0.15;
          if (pos.y < mouthCenterY && pos.y > -0.5) {
              float weight = smoothstep(-0.5, mouthCenterY, pos.y);
              pos.y -= jawOpen * 0.08 * weight; 
              pos.z -= jawOpen * 0.03 * weight;
          }
          vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
          gl_Position = projectionMatrix * mvPosition;
          
          float s = (scale.x + scale.y + scale.z) / 3.0;
          if (s < 0.001) s = 0.02;
          gl_PointSize = s * 850.0 / -mvPosition.z; 
          
          vColor = color;
          vAlpha = opacity;
        }
      `,
      fragmentShader: `
        varying vec3 vColor;
        varying float vAlpha;
        void main() {
          vec2 coord = gl_PointCoord - vec2(0.5);
          if (dot(coord, coord) > 0.25) discard;
          gl_FragColor = vec4(vColor, vAlpha);
        }
      `,
      uniforms: { jawOpen: { value: 0.0 } },
      transparent: true,
      depthTest: true,
      depthWrite: true
    });
  }

  private setupGeometryFromPLY(buffer: ArrayBuffer) {
    const textDecoder = new TextDecoder();
    const headerLength = this.findHeaderEnd(buffer);
    const headerStr = textDecoder.decode(buffer.slice(0, headerLength));
    const vertexCount = parseInt(/element vertex (\d+)/.exec(headerStr)![1]);
    const bodyView = new DataView(buffer, headerLength);
    const props = headerStr.match(/property float (\w+)/g) || [];
    const stride = props.length * 4; 
    const propNames = props.map(p => p.split(' ')[2]);
    
    const idx = {
      x: propNames.indexOf('x'), y: propNames.indexOf('y'), z: propNames.indexOf('z'),
      r: propNames.indexOf('f_dc_0'), g: propNames.indexOf('f_dc_1'), b: propNames.indexOf('f_dc_2'),
      sx: propNames.indexOf('scale_0'), op: propNames.indexOf('opacity')
    };

    let minX = Infinity, minY = Infinity, minZ = Infinity, maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let i = 0; i < vertexCount; i++) {
        const b = i * stride;
        const x = bodyView.getFloat32(b + idx.x * 4, true);
        const y = bodyView.getFloat32(b + idx.y * 4, true);
        const z = bodyView.getFloat32(b + idx.z * 4, true);
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
    }

    const height = maxY - minY;
    const cropLine = maxY - (height * 0.45);
    const sigmoid = (x: number) => 1 / (1 + Math.exp(-x));
    const SH_C0 = 0.28209479177387814;

    const keptIndices = [];
    let kMinY = Infinity, kMaxY = -Infinity, kMinX = Infinity, kMaxX = -Infinity;

    for (let i = 0; i < vertexCount; i++) {
        const y = bodyView.getFloat32(i * stride + idx.y * 4, true);
        if (y > cropLine) {
            keptIndices.push(i);
            const x = bodyView.getFloat32(i * stride + idx.x * 4, true);
            if (y < kMinY) kMinY = y; if (y > kMaxY) kMaxY = y;
            if (x < kMinX) kMinX = x; if (x > kMaxX) kMaxX = x;
        }
    }

    const scaleFactor = 1.6 / (kMaxY - kMinY || 1);
    const cX = (kMinX + kMaxX) / 2, cY = (kMinY + kMaxY) / 2;

    const pos = new Float32Array(keptIndices.length * 3);
    const col = new Float32Array(keptIndices.length * 3);
    const sca = new Float32Array(keptIndices.length * 3);
    const opa = new Float32Array(keptIndices.length);

    keptIndices.forEach((i, n) => {
        const b = i * stride;
        pos[n*3] = (bodyView.getFloat32(b + idx.x * 4, true) - cX) * scaleFactor;
        pos[n*3+1] = (bodyView.getFloat32(b + idx.y * 4, true) - cY) * scaleFactor;
        pos[n*3+2] = (bodyView.getFloat32(b + idx.z * 4, true) - minZ) * scaleFactor * -1 + 0.5;

        col[n*3] = Math.min(1, (0.5 + SH_C0 * bodyView.getFloat32(b + idx.r * 4, true)) * 1.3);
        col[n*3+1] = Math.min(1, (0.5 + SH_C0 * bodyView.getFloat32(b + idx.g * 4, true)) * 1.3);
        col[n*3+2] = Math.min(1, (0.5 + SH_C0 * bodyView.getFloat32(b + idx.b * 4, true)) * 1.3);

        const s = Math.exp(bodyView.getFloat32(b + idx.sx * 4, true));
        sca[n*3] = sca[n*3+1] = sca[n*3+2] = s;
        opa[n] = sigmoid(bodyView.getFloat32(b + idx.op * 4, true));
    });

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    this.geometry.setAttribute('color', new THREE.BufferAttribute(col, 3));
    this.geometry.setAttribute('scale', new THREE.BufferAttribute(sca, 3));
    this.geometry.setAttribute('opacity', new THREE.BufferAttribute(opa, 1));

    if (this.points) this.scene.remove(this.points);
    this.points = new THREE.Points(this.geometry, this.material!);
    this.scene.add(this.points);
  }

  private findHeaderEnd(buffer: ArrayBuffer): number {
    const view = new Uint8Array(buffer);
    const target = new TextEncoder().encode("end_header");
    for (let i = 0; i < view.length - target.length; i++) {
      if (view[i] === target[0] && view[i+10] === target[10]) return i + 11;
    }
    return 0;
  }

  public updateLipSync(audioLevel: number) {
    const targetOpen = Math.min(1.0, Math.max(0, (audioLevel - 0.02) * 3.0)); 
    this.jawOpenAmount += (targetOpen - this.jawOpenAmount) * 0.3;
  }

  private animate() {
    requestAnimationFrame(this.animate.bind(this));
    if (this.material) this.material.uniforms.jawOpen.value = this.jawOpenAmount;
    this.renderer.render(this.scene, this.camera);
  }

  private onResize() {
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.camera.aspect = this.container.clientWidth / this.container.clientHeight;
    this.camera.updateProjectionMatrix();
  }
}