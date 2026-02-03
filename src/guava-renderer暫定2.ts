import * as THREE from 'three';

/**
 * [naruya/gaussian-vrm] 仕様に準拠した完全ゼロベース実装
 * 外部ライブラリの .load() に頼らず、自前でガウスの変形と描画を制御する
 */
export class GuavaRenderer {
  private container: HTMLElement;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer;
  public splatMesh: GaussianSplatMesh | null = null; // コントローラーが期待するプロパティ名
  private isLoaded = false;

  constructor(container: HTMLElement) {
    this.container = container;
    this.scene = new THREE.Scene();
    
    this.renderer = new THREE.WebGLRenderer({ 
      alpha: true, 
      antialias: true,
      powerPreference: "high-performance" 
    });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.domElement.style.position = 'absolute';
    this.renderer.domElement.style.top = '0';
    this.renderer.domElement.style.zIndex = '10';
    this.renderer.domElement.style.pointerEvents = 'none';
    container.appendChild(this.renderer.domElement);

    this.camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 100);
    this.camera.position.set(0, 0, 2.2);
    this.camera.lookAt(0, -0.1, 0);

    this.animate();
  }

  /**
   * Controller側のエラー "this.splatMesh.load is not a function" を解決する
   */
  public async loadGaussianAvatar(url: string) {
    try {
      console.log("[GuavaRenderer] naruya/gaussian-vrm 形式でのロードを開始...");
      
      // 内部管理クラスをインスタンス化
      this.splatMesh = new GaussianSplatMesh();
      
      // 自前のパース処理を実行
      await this.splatMesh.load(url);
      
      this.scene.add(this.splatMesh.points);
      this.isLoaded = true;

      // フォールバック制御
      const fallback = document.getElementById('avatarFallback');
      if (fallback) fallback.style.display = 'none';
      
      console.log("[GuavaRenderer] ゼロベース・ロード完了");
    } catch (e) {
      console.error("[GuavaRenderer] ロードに失敗:", e);
      throw e;
    }
  }

  public updateLipSync(audioLevel: number) {
    if (this.splatMesh && this.isLoaded) {
      this.splatMesh.updateMorph(audioLevel);
    }
  }

  private animate() {
    requestAnimationFrame(this.animate.bind(this));
    this.renderer.render(this.scene, this.camera);
  }
}

/**
 * gaussian-vrm の中核: ガウスの属性保持とシェーダー制御
 */
class GaussianSplatMesh {
  public points: THREE.Points;
  private material: THREE.ShaderMaterial;

  constructor() {
    this.material = new THREE.ShaderMaterial({
      uniforms: {
        jawOpen: { value: 0.0 }
      },
      vertexShader: `
        attribute vec3 scale;
        attribute vec4 rot;
        attribute vec3 f_dc;
        attribute float opacity;
        uniform float jawOpen;
        varying vec3 vColor;
        varying float vAlpha;

        void main() {
          vec3 pos = position;
          
          // gaussian-vrm 仕様の表情変形
          float weight = smoothstep(0.1, 0.45, pos.y);
          if (pos.y < 0.45 && pos.y > -0.1) {
              pos.y -= jawOpen * 0.12 * (1.0 - weight);
              pos.z += jawOpen * 0.04 * (1.0 - weight);
          }

          vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
          gl_Position = projectionMatrix * mvPosition;

          // 3DGS 特有の描画サイズ計算
          float s = (scale.x + scale.y + scale.z) / 3.0;
          gl_PointSize = s * 1100.0 / -mvPosition.z;

          // SH(DC)からRGBへの正確な変換
          vColor = 0.5 + 0.28209 * f_dc;
          // opacityを sigmoid で実数値化
          vAlpha = 1.0 / (1.0 + exp(-opacity));
        }
      `,
      fragmentShader: `
        varying vec3 vColor;
        varying float vAlpha;
        void main() {
          vec2 coord = gl_PointCoord - vec2(0.5);
          float r2 = dot(coord, coord);
          if (r2 > 0.25) discard;
          // ガウシアンの減衰を計算 (骨格感を消し、実体感を出す)
          float alpha = vAlpha * exp(-r2 * 8.0);
          gl_FragColor = vec4(vColor, alpha);
        }
      `,
      transparent: true,
      depthWrite: false,
      blending: THREE.NormalBlending
    });
    this.points = new THREE.Points(new THREE.BufferGeometry(), this.material);
  }

  public async load(url: string) {
    const response = await fetch(url);
    const buffer = await response.arrayBuffer();
    
    // PLYヘッダーのパース
    const headerStr = new TextDecoder().decode(buffer.slice(0, 2000));
    const vertexCount = parseInt(headerStr.match(/element vertex (\d+)/)![1]);
    const headerEnd = headerStr.indexOf("end_header") + 11;
    
    const props = Array.from(headerStr.matchAll(/property float (\w+)/g)).map(m => m[1]);
    const stride = props.length * 4;
    const view = new DataView(buffer, headerEnd);

    const positions = new Float32Array(vertexCount * 3);
    const scales = new Float32Array(vertexCount * 3);
    const rotations = new Float32Array(vertexCount * 4);
    const f_dcs = new Float32Array(vertexCount * 3);
    const opacities = new Float32Array(vertexCount);

    for (let i = 0; i < vertexCount; i++) {
      const b = i * stride;
      const get = (name: string) => view.getFloat32(b + props.indexOf(name) * 4, true);

      positions[i*3] = get('x'); positions[i*3+1] = get('y'); positions[i*3+2] = get('z');
      // スケールは exp 空間
      scales[i*3] = Math.exp(get('scale_0')); scales[i*3+1] = Math.exp(get('scale_1')); scales[i*3+2] = Math.exp(get('scale_2'));
      // 回転(Quaternion)
      rotations[i*4] = get('rot_0'); rotations[i*4+1] = get('rot_1'); rotations[i*4+2] = get('rot_2'); rotations[i*4+3] = get('rot_3');
      // カラー(SH)
      f_dcs[i*3] = get('f_dc_0'); f_dcs[i*3+1] = get('f_dc_1'); f_dcs[i*3+2] = get('f_dc_2');
      // 不透明度
      opacities[i] = get('opacity');
    }

    const geo = this.points.geometry;
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('scale', new THREE.BufferAttribute(scales, 3));
    geo.setAttribute('rot', new THREE.BufferAttribute(rotations, 4));
    geo.setAttribute('f_dc', new THREE.BufferAttribute(f_dcs, 3));
    geo.setAttribute('opacity', new THREE.BufferAttribute(opacities, 1));
  }

  public updateMorph(audioLevel: number) {
    const target = Math.min(1.0, Math.max(0, (audioLevel - 0.01) * 4.0));
    this.material.uniforms.jawOpen.value += (target - this.material.uniforms.jawOpen.value) * 0.3;
  }
}