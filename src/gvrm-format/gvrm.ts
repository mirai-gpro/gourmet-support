// src/gvrm-format/gvrm.ts
import * as THREE from 'three';
import { PLYLoader } from './ply';
import { GSViewer } from './gs';
import { VRMManager } from './vrm';
import { NeuralRefiner } from './neural-refiner';
import { TemplateDecoder } from './template-decoder';
import { ImageEncoder } from './image-encoder';
import { WebGLDisplay } from './webgl-display';

export class GVRM {
    private scene = new THREE.Scene();
    private camera: THREE.PerspectiveCamera;
    private renderer: THREE.WebGLRenderer;
    private renderTarget: THREE.WebGLRenderTarget;
    private refiner = new NeuralRefiner();
    private templateDecoder = new TemplateDecoder();
    private imageEncoder = new ImageEncoder();
    private vrm = new VRMManager();
    public viewer: GSViewer | null = null;
    private webglDisplay: WebGLDisplay;
    
    private idEmbedding = new Float32Array(256).fill(0.5);
    private isReady = false;

    constructor(container: HTMLElement) {
        this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        this.renderer.setSize(256, 256);
        this.renderer.domElement.style.display = 'none';
        container.appendChild(this.renderer.domElement);

        this.webglDisplay = new WebGLDisplay(container, 512, 512);

        this.camera = new THREE.PerspectiveCamera(
            45, 
            container.clientWidth / container.clientHeight, 
            0.01, 
            100
        );
        this.camera.position.set(0, 1.4, 0.8);
        
        this.renderTarget = new THREE.WebGLRenderTarget(256, 256, { 
            type: THREE.FloatType,
            format: THREE.RGBAFormat
        });

        window.addEventListener('resize', () => {
            this.camera.aspect = container.clientWidth / container.clientHeight;
            this.camera.updateProjectionMatrix();
        });

        this.animate();
    }

    public async loadAssets(plyUrl: string) {
        console.log('[GVRM] Loading assets...');
        
        try {
            const data = await PLYLoader.load(plyUrl);
            console.log('[GVRM] PLY loaded, vertex count:', data.positions.length / 3);
            
            this.camera.position.set(0, 1.4, 0.8);
            this.camera.lookAt(0, 1.4, 0);
            
            console.log('[GVRM] Initializing Template Decoder...');
            await this.templateDecoder.init('/assets');
            
            console.log('[GVRM] Initializing Image Encoder (DINOv2)...');
            await this.imageEncoder.init();
            
            console.log('[GVRM] Initializing Neural Refiner...');
            await this.refiner.init();
            
            const TEMPLATE_VERTEX_COUNT = 10595;
            const plyVertexCount = data.positions.length / 3;
            
            console.log('[GVRM] Generating 32-channel latents via Template Decoder...', {
                plyVertices: plyVertexCount,
                templateVertices: TEMPLATE_VERTEX_COUNT
            });
            
            console.log('[GVRM] Extracting features from source image...');
            const projectionFeature = await this.imageEncoder.extractFeatures(
                '/assets/source.png',
                TEMPLATE_VERTEX_COUNT
            );
            
            const templateOutput = await this.templateDecoder.generate(
                projectionFeature,
                this.idEmbedding
            );
            
            console.log('[GVRM] Template Decoder output:', {
                latent32ch: templateOutput.latent32ch.length,
                expectedLength: TEMPLATE_VERTEX_COUNT * 32
            });
            
            const latents = new Float32Array(plyVertexCount * 32);
            
            const geometryData = this.templateDecoder.getGeometryData();
            if (!geometryData) {
                throw new Error('Failed to get geometry data from Template Decoder');
            }
            
            const templatePositions = geometryData.vTemplate;
            
            for (let i = 0; i < plyVertexCount; i++) {
                const px = data.positions[i * 3];
                const py = data.positions[i * 3 + 1];
                const pz = data.positions[i * 3 + 2];
                
                let minDist = Infinity;
                let nearestIdx = 0;
                
                for (let j = 0; j < TEMPLATE_VERTEX_COUNT; j++) {
                    const tx = templatePositions[j * 3];
                    const ty = templatePositions[j * 3 + 1];
                    const tz = templatePositions[j * 3 + 2];
                    
                    const dist = (px - tx) ** 2 + (py - ty) ** 2 + (pz - tz) ** 2;
                    
                    if (dist < minDist) {
                        minDist = dist;
                        nearestIdx = j;
                    }
                }
                
                for (let ch = 0; ch < 32; ch++) {
                    latents[i * 32 + ch] = templateOutput.latent32ch[nearestIdx * 32 + ch];
                }
            }
            
            console.log('[GVRM] Mapped template features to PLY vertices:', {
                plyVertexCount,
                latentsLength: latents.length,
                expectedLength: plyVertexCount * 32
            });
            
            this.viewer = new GSViewer({
                positions: data.positions,
                colors: data.colors,
                boneIndices: data.boneIndices,
                boneWeights: data.boneWeights,
                vertexCount: plyVertexCount,
                latents
            });
            this.scene.add(this.viewer.mesh);
            
            console.log('[GVRM] Assets loaded successfully');
            this.isReady = true;
            
            console.log('[GVRM] Attempting to remove loading indicators...');
            
            const possibleIds = ['avatarFallback', 'loadingIndicator', 'loading', 'spinner', 'avatarLoading'];
            let removed = 0;
            
            possibleIds.forEach(id => {
                const el = document.getElementById(id);
                if (el) {
                    console.log(`[GVRM] Found element with id: ${id}`);
                    el.remove();
                    removed++;
                }
            });
            
            const loadingElements = document.querySelectorAll(
                '.loading, .spinner, .loading-indicator, [class*="loading"], [class*="spinner"]'
            );
            console.log(`[GVRM] Found ${loadingElements.length} elements by class`);
            loadingElements.forEach(el => {
                if (el instanceof HTMLElement) {
                    el.remove();
                    removed++;
                }
            });
            
            console.log(`[GVRM] Removed ${removed} loading indicator(s)`);
            
            return true;
        } catch (e) {
            console.error('[GVRM] Failed to load assets:', e);
            return false;
        }
    }

    private frameCount = 0;
    private lastRefinedRgb: Float32Array | null = null;
    private isFirstFrameProcessed = false;

    private async animate() {
        requestAnimationFrame(() => this.animate());
        
        if (!this.viewer || !this.isReady) {
            return;
        }

        this.frameCount++;

        if (this.frameCount === 1) {
            console.log('[GVRM] First frame rendering...');
        }

        this.viewer.updateBones(this.vrm.update());

        const coarseFm = new Float32Array(1 * 32 * 256 * 256);
        
        if (this.frameCount === 1) {
            console.log('[GVRM] Generating coarse feature map (8 passes)...');
        }
        
        for (let i = 0; i < 8; i++) {
            this.viewer.updateLatentTile(i);
            this.renderer.setRenderTarget(this.renderTarget);
            this.renderer.render(this.scene, this.camera);
            
            const pixels = new Float32Array(256 * 256 * 4);
            this.renderer.readRenderTargetPixels(this.renderTarget, 0, 0, 256, 256, pixels);
            
            if (this.frameCount === 1 && i === 0) {
                const pixelStats = {
                    min: Math.min(...Array.from(pixels.slice(0, 1000))),
                    max: Math.max(...Array.from(pixels.slice(0, 1000))),
                    sample: Array.from(pixels.slice(0, 10)).map(v => v.toFixed(6))
                };
                console.log(`[GVRM] Pass ${i} pixel data:`, pixelStats);
            }
            
            const baseOffset = i * 4 * 256 * 256;
            
            for (let p = 0; p < 256 * 256; p++) {
                coarseFm[baseOffset + p] = pixels[p * 4 + 0];
                coarseFm[baseOffset + 256 * 256 + p] = pixels[p * 4 + 1];
                coarseFm[baseOffset + 256 * 256 * 2 + p] = pixels[p * 4 + 2];
                coarseFm[baseOffset + 256 * 256 * 3 + p] = pixels[p * 4 + 3];
            }
        }

        if (!this.isFirstFrameProcessed) {
            console.log('[GVRM] Calling Neural Refiner...');
            
            const startTime = performance.now();
            const refinedRgb = await this.refiner.process(coarseFm, this.idEmbedding);
            const elapsed = performance.now() - startTime;
            
            console.log(`[GVRM] Neural Refiner took ${elapsed.toFixed(2)}ms`);
            
            if (refinedRgb) {
                this.lastRefinedRgb = refinedRgb;
                console.log('[GVRM] ✅ Neural Refiner completed');
                console.log('[GVRM] refinedRgb length:', refinedRgb.length);
                console.log('[GVRM] Sample values:', Array.from(refinedRgb.slice(0, 10)).map(v => v.toFixed(3)));
            }
            this.isFirstFrameProcessed = true;
        }
        
        if (this.lastRefinedRgb) {
            if (this.frameCount <= 5) {
                console.log(`[GVRM] Frame ${this.frameCount}: Drawing via WebGL`);
            }
            this.webglDisplay.display(this.lastRefinedRgb, this.frameCount);
        } else {
            if (this.frameCount <= 5) {
                console.warn(`[GVRM] Frame ${this.frameCount}: No refined RGB data`);
            }
        }
    }

    public updateLipSync(level: number) {
        this.vrm.setLipSync(level);
    }
}