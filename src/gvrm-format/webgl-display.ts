// webgl-display.ts
// Neural Refiner出力をWebGLで直接表示（デバッグ強化版・レイアウト修正版）

import * as THREE from 'three';

export class WebGLDisplay {
  private scene: THREE.Scene;
  private camera: THREE.OrthographicCamera;
  private renderer: THREE.WebGLRenderer;
  private texture: THREE.DataTexture;
  private quad: THREE.Mesh;
  private shaderMaterial: THREE.ShaderMaterial;
  
  constructor(container: HTMLElement, width: number = 512, height: number = 512) {
    console.log('[WebGLDisplay] Initializing...');
    console.log('[WebGLDisplay] Container:', container.id || container.tagName);
    
    // シーン setup
    this.scene = new THREE.Scene();
    
    // 正投影カメラ（2D描画用）
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    
    // レンダラー
    this.renderer = new THREE.WebGLRenderer({ 
      antialias: false,
      alpha: false,
      premultipliedAlpha: false
    });
    this.renderer.setSize(width, height);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.renderer.domElement.style.position = 'absolute';
    this.renderer.domElement.style.top = '0';
    this.renderer.domElement.style.left = '0';
    this.renderer.domElement.style.zIndex = '10';
    this.renderer.domElement.style.objectFit = 'contain';
    this.renderer.domElement.style.minHeight = '400px'; // ✅ 最小高さを確保
    this.renderer.domElement.style.border = '2px solid red'; // デバッグ用
    container.appendChild(this.renderer.domElement);
    
    console.log('[WebGLDisplay] Canvas appended to container');
    console.log('[WebGLDisplay] Canvas dimensions:', {
      width: this.renderer.domElement.width,
      height: this.renderer.domElement.height,
      clientWidth: this.renderer.domElement.clientWidth,
      clientHeight: this.renderer.domElement.clientHeight
    });
    
    // DataTexture作成
    const emptyData = new Float32Array(width * height * 4);
    this.texture = new THREE.DataTexture(
      emptyData,
      width,
      height,
      THREE.RGBAFormat,
      THREE.FloatType
    );
    this.texture.minFilter = THREE.LinearFilter;
    this.texture.magFilter = THREE.LinearFilter;
    this.texture.generateMipmaps = false;
    this.texture.needsUpdate = true;
    
    // フルスクリーンクアッド
    const geometry = new THREE.PlaneGeometry(2, 2);
    this.shaderMaterial = new THREE.ShaderMaterial({
      uniforms: {
        tDiffuse: { value: this.texture },
        uMinVal: { value: 0.0 },
        uMaxVal: { value: 1.0 },
        uBrightness: { value: 2.0 } // デフォルト明度
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform sampler2D tDiffuse;
        uniform float uMinVal;
        uniform float uMaxVal;
        uniform float uBrightness;
        varying vec2 vUv;
        
        void main() {
          // Y軸反転
          vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
          vec4 texColor = texture2D(tDiffuse, uv);
          vec3 color = texColor.rgb;
          
          // 動的コントラスト調整
          float range = uMaxVal - uMinVal;
          if (range > 0.01) {
            color = (color - uMinVal) / range;
          }
          
          // 明度調整
          color = color * uBrightness;
          
          // 範囲クランプ
          color = clamp(color, 0.0, 1.0);
          
          gl_FragColor = vec4(color, 1.0);
        }
      `,
      depthTest: false,
      depthWrite: false
    });
    
    this.quad = new THREE.Mesh(geometry, this.shaderMaterial);
    this.scene.add(this.quad);
    
    console.log('[WebGLDisplay] ✅ Initialized');
  }
  
  public display(data: Float32Array, frameCount: number = 0): void {
    const width = 512;
    const height = 512;
    const expectedLength = width * height * 3;
    
    if (data.length !== expectedLength) {
      console.error(`[WebGLDisplay] Invalid data length: ${data.length}, expected: ${expectedLength}`);
      return;
    }
    
    // データレイアウト変換
    const pixels = new Float32Array(width * height * 4);
    const rOffset = 0;
    const gOffset = width * height;
    const bOffset = width * height * 2;
    
    for (let i = 0; i < width * height; i++) {
      pixels[i * 4 + 0] = data[rOffset + i];
      pixels[i * 4 + 1] = data[gOffset + i];
      pixels[i * 4 + 2] = data[bOffset + i];
      pixels[i * 4 + 3] = 1.0;
    }
    
    if (frameCount === 1) {
      console.log('[WebGLDisplay] ========== FIRST FRAME DEBUG ==========');
      
      // 統計情報
      const rgbOnly = pixels.filter((_, i) => i % 4 !== 3);
      const stats = {
        min: Math.min(...Array.from(rgbOnly.slice(0, 1500))),
        max: Math.max(...Array.from(rgbOnly.slice(0, 1500))),
        avg: Array.from(rgbOnly.slice(0, 1500)).reduce((a, b) => a + b, 0) / 1500
      };
      console.log('[WebGLDisplay] RGB stats:', stats);
      
      // シェーダーパラメータを更新
      this.shaderMaterial.uniforms.uMinVal.value = stats.min;
      this.shaderMaterial.uniforms.uMaxVal.value = stats.max;
      console.log('[WebGLDisplay] Shader params:', {
        min: stats.min.toFixed(3),
        max: stats.max.toFixed(3),
        brightness: this.shaderMaterial.uniforms.uBrightness.value
      });
    }
    
    // テクスチャ更新
    this.texture.image.data = pixels;
    this.texture.needsUpdate = true;
    
    // レンダリング
    this.renderer.render(this.scene, this.camera);
    
    if (frameCount === 1) {
      // Canvas状態を確認
      const canvas = this.renderer.domElement;
      console.log('[WebGLDisplay] Canvas state:', {
        width: canvas.width,
        height: canvas.height,
        styleWidth: canvas.style.width,
        styleHeight: canvas.style.height,
        position: canvas.style.position,
        zIndex: canvas.style.zIndex,
        display: canvas.style.display,
        visibility: canvas.style.visibility,
        opacity: canvas.style.opacity,
        parentElement: canvas.parentElement?.tagName,
        isConnected: canvas.isConnected,
        offsetWidth: canvas.offsetWidth,
        offsetHeight: canvas.offsetHeight
      });
      
      // 実際のピクセルデータを読み取って確認
      try {
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          console.warn('[WebGLDisplay] Cannot get 2D context from WebGL canvas');
        }
        
        const dataURL = canvas.toDataURL('image/png');
        console.log('[WebGLDisplay] DataURL generated:', {
          length: dataURL.length,
          preview: dataURL.substring(0, 50) + '...'
        });
        
        // DataURLをコンソールに出力（ブラウザで開いて確認可能）
        console.log('[WebGLDisplay] Copy this URL to browser to view:', dataURL.substring(0, 200));
      } catch (e) {
        console.error('[WebGLDisplay] Failed to generate DataURL:', e);
      }
      
      console.log('[WebGLDisplay] ========== END FIRST FRAME DEBUG ==========');
    }
  }
  
  public setBrightness(value: number): void {
    this.shaderMaterial.uniforms.uBrightness.value = value;
    console.log('[WebGLDisplay] Brightness updated to:', value);
  }
  
  public resize(width: number, height: number): void {
    this.renderer.setSize(width, height);
  }
  
  public dispose(): void {
    this.texture.dispose();
    this.quad.geometry.dispose();
    this.shaderMaterial.dispose();
    this.renderer.dispose();
    console.log('[WebGLDisplay] Disposed');
  }
}