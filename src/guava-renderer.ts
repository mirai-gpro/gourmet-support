import * as THREE from 'three';
import { GVRM } from '../../gvrm-format/gvrm';

/**
 * [GUAVA仕様] Renderer
 * 学習データ(40枚のPNG)と3DGSモデルを完全同期させる
 */
export class GuavaRenderer {
  private container: HTMLElement;
  private scene: THREE.Scene;
  private camera: THREE.PerspectiveCamera;
  private renderer: THREE.WebGLRenderer;
  public splatMesh: GVRM | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.container.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.position = 'absolute';
    this.renderer.domElement.style.zIndex = '100';

    // GUAVAの標準カメラ角 
    this.camera = new THREE.PerspectiveCamera(35, container.clientWidth / container.clientHeight, 0.1, 100);
    this.camera.position.set(0, 0, 3.2); 
    this.camera.lookAt(0, 0, 0);

    this.animate();
  }

  public async loadAssets(url: string): Promise<boolean> {
    try {
      this.splatMesh = new GVRM();
      const mesh = await this.splatMesh.load(url);
      
      // LBSスキニングにより、アバターは自動的に正しい位置(0,0,0)に現れる
      mesh.position.set(0, -0.65, 0);
      this.scene.add(mesh);

      // 40枚のPNGの中から source.png (基準) を表示
      const bg = document.querySelector('img[src*="source.png"]') as HTMLImageElement;
      if (bg) {
        bg.style.display = 'block';
        bg.style.position = 'absolute';
        bg.style.zIndex = '1';
      }
      return true;
    } catch (e) { return false; }
  }

  public updateLipSync(level: number) {
    if (this.splatMesh) this.splatMesh.update(level);
  }

  private animate() {
    requestAnimationFrame(this.animate.bind(this));
    this.renderer.render(this.scene, this.camera);
  }
}