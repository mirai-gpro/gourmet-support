// src/gvrm-format/gvrm.ts
import * as THREE from 'three';
import { PLYLoader } from './ply';
import { GSViewer } from './gs';
import { VRMManager } from './vrm';
import { NeuralRefiner } from './neural-refiner';
import { TemplateDecoder } from './template-decoder';
import { ImageEncoder } from './image-encoder';
import type { SourceCameraConfig } from './image-encoder';
import { WebGLDisplay } from './webgl-display';

export interface GVRMConfig {
    templatePath: string;
    imagePath: string;
}

export class GVRM {
    private scene = new THREE.Scene();
    private camera!: THREE.PerspectiveCamera;
    private renderer: THREE.WebGLRenderer | null = null;
    private renderTarget!: THREE.WebGLRenderTarget;
    private refiner = new NeuralRefiner();
    private templateDecoder = new TemplateDecoder();
    private imageEncoder = new ImageEncoder();
    private vrm = new VRMManager();
    public viewer: GSViewer | null = null;
    private webglDisplay: WebGLDisplay | null = null;
    private container: HTMLElement | null = null;

    private idEmbedding: Float32Array = new Float32Array(256).fill(0.5);
    private isReady = false;
    private isDisabled = false;

    /**
     * コンストラクタ - 引数なしでも動作（init()で初期化）
     */
    constructor(container?: HTMLElement) {
        console.log('[GVRM] Constructor called, container:', container ? 'provided' : 'not provided');

        // containerが渡された場合は即座に初期化
        if (container) {
            this.setupContainer(container);
        }
        // containerがない場合はinit()で初期化される
    }

    /**
     * コンテナのセットアップ
     */
    private setupContainer(container: HTMLElement): void {
        console.log('[GVRM] Setting up container:', container.id, container.tagName);
        this.container = container;

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

    /**
     * 初期化メソッド - configを受け取ってアセットをロード
     * 本番環境のconcierge-controller.tsから呼ばれる
     */
    public async init(config?: GVRMConfig): Promise<void> {
        console.log('[GVRM] init() called with config:', config);

        // コンテナがまだセットアップされていない場合はDOMから検索
        if (!this.container) {
            const found = document.getElementById('avatar3DContainer');
            if (found) {
                console.log('[GVRM] Found #avatar3DContainer via DOM search');
                this.setupContainer(found as HTMLElement);
            } else {
                console.error('[GVRM] #avatar3DContainer not found in DOM!');
                this.isDisabled = true;
                throw new Error('[GVRM] Container not found');
            }
        }

        // configが渡された場合はアセットをロード
        if (config) {
            await this.loadAssets(config.templatePath, config.imagePath);
        }
    }

    public async loadAssets(plyUrl: string, imageUrl?: string): Promise<boolean> {
        // 無効化モードの場合は早期リターン
        if (this.isDisabled) {
            console.warn('[GVRM] Disabled mode - skipping asset loading');
            return false;
        }

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

            // テンプレートジオメトリデータを取得
            const geometryDataForEncoder = this.templateDecoder.getGeometryData();
            if (!geometryDataForEncoder) {
                throw new Error('Failed to get geometry data for Image Encoder');
            }
            const templateVertices = geometryDataForEncoder.vTemplate;

            console.log('[GVRM] Using source camera projection with', TEMPLATE_VERTEX_COUNT, 'vertices');

            // ソースカメラ設定をロード（position/target/fov形式）
            const sourceCameraResponse = await fetch('/assets/source_camera.json');
            const sourceCameraConfig: SourceCameraConfig = await sourceCameraResponse.json();

            console.log('[GVRM] Source camera config loaded:', {
                position: sourceCameraConfig.position,
                target: sourceCameraConfig.target,
                fov: sourceCameraConfig.fov
            });

            // extractFeaturesWithSourceCameraを使用（カメラ行列は内部で構築）
            const { projectionFeature, idEmbedding } = await this.imageEncoder.extractFeaturesWithSourceCamera(
                '/assets/source.png',
                sourceCameraConfig,
                templateVertices,
                TEMPLATE_VERTEX_COUNT,
                128  // feature dimension
            );

            // DINOv2から抽出したID embeddingを使用
            this.idEmbedding = idEmbedding;

            const templateOutput = await this.templateDecoder.generate(
                projectionFeature,
                this.idEmbedding
            );

            console.log('[GVRM] Template Decoder output:', {
                latent32ch: templateOutput.latent32ch.length,
                opacity: templateOutput.opacity.length,
                scale: templateOutput.scale.length,
                rotation: templateOutput.rotation.length,
                expectedLatentLength: TEMPLATE_VERTEX_COUNT * 32
            });

            // PLY頂点用の配列を作成
            const latents = new Float32Array(plyVertexCount * 32);
            const opacity = new Float32Array(plyVertexCount);
            const scale = new Float32Array(plyVertexCount * 3);
            const rotation = new Float32Array(plyVertexCount * 4);

            // 事前計算された頂点マッピングを読み込み（O(N²) → O(N)に最適化）
            console.log('[GVRM] Loading pre-computed vertex mapping...');
            const mappingStartTime = performance.now();

            let vertexMapping: number[];
            try {
                const mappingResponse = await fetch('/assets/vertex_mapping.json');
                if (mappingResponse.ok) {
                    const mappingData = await mappingResponse.json();
                    vertexMapping = mappingData.mapping;
                    console.log('[GVRM] ✅ Pre-computed mapping loaded:', {
                        plyVertexCount: mappingData.plyVertexCount,
                        templateVertexCount: mappingData.templateVertexCount
                    });
                } else {
                    throw new Error('Mapping file not found');
                }
            } catch (e) {
                // フォールバック: ランタイムで計算（遅い）
                console.warn('[GVRM] ⚠️ Pre-computed mapping not found, computing at runtime (slow)...');
                const geometryData = this.templateDecoder.getGeometryData();
                if (!geometryData) {
                    throw new Error('Failed to get geometry data from Template Decoder');
                }
                const templatePositions = geometryData.vTemplate;

                vertexMapping = new Array(plyVertexCount);
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
                    vertexMapping[i] = nearestIdx;
                }
            }

            const mappingElapsed = performance.now() - mappingStartTime;
            console.log(`[GVRM] Vertex mapping ready in ${mappingElapsed.toFixed(2)}ms`);

            // マッピングを使用してTemplate Decoder出力をPLY頂点に転写（O(N)）
            for (let i = 0; i < plyVertexCount; i++) {
                const nearestIdx = vertexMapping[i];

                // latent32ch [32 per vertex]
                for (let ch = 0; ch < 32; ch++) {
                    latents[i * 32 + ch] = templateOutput.latent32ch[nearestIdx * 32 + ch];
                }

                // opacity [1 per vertex]
                opacity[i] = templateOutput.opacity[nearestIdx];

                // scale [3 per vertex]
                for (let s = 0; s < 3; s++) {
                    scale[i * 3 + s] = templateOutput.scale[nearestIdx * 3 + s];
                }

                // rotation [4 per vertex]
                for (let r = 0; r < 4; r++) {
                    rotation[i * 4 + r] = templateOutput.rotation[nearestIdx * 4 + r];
                }
            }

            console.log('[GVRM] Mapped template features to PLY vertices:', {
                plyVertexCount,
                latentsLength: latents.length,
                opacityLength: opacity.length,
                scaleLength: scale.length
            });

            // GSViewer作成（Gaussian属性付き）
            this.viewer = new GSViewer({
                positions: data.positions,
                latents,
                opacity,
                scale,
                rotation,
                boneIndices: data.boneIndices,
                boneWeights: data.boneWeights,
                vertexCount: plyVertexCount
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

    /**
     * Coarse Feature Mapの正規化
     * GSViewerのadditive blendingで蓄積された値を-1〜+1に正規化
     */
    private normalizeCoarseFeatureMap(coarseFm: Float32Array): Float32Array {
        const normalized = new Float32Array(coarseFm.length);
        const numChannels = 32;
        const spatialSize = 256 * 256;

        // チャンネルごとに正規化（Instance Normalization的アプローチ）
        for (let ch = 0; ch < numChannels; ch++) {
            const offset = ch * spatialSize;

            // 統計量を計算
            let sum = 0;
            let sumSq = 0;
            let validCount = 0;

            for (let i = 0; i < spatialSize; i++) {
                const val = coarseFm[offset + i];
                if (isFinite(val)) {
                    sum += val;
                    sumSq += val * val;
                    validCount++;
                }
            }

            if (validCount === 0) {
                // 全て無効値の場合はゼロ埋め
                for (let i = 0; i < spatialSize; i++) {
                    normalized[offset + i] = 0;
                }
                continue;
            }

            const mean = sum / validCount;
            const variance = (sumSq / validCount) - (mean * mean);
            const std = Math.sqrt(Math.max(variance, 1e-8)); // 安定性のため最小値設定

            // 正規化してtanhで-1〜+1にクリップ
            for (let i = 0; i < spatialSize; i++) {
                const val = coarseFm[offset + i];
                if (isFinite(val)) {
                    const normalizedVal = (val - mean) / std;
                    // tanhで滑らかに-1〜+1にマッピング
                    normalized[offset + i] = Math.tanh(normalizedVal * 0.5);
                } else {
                    normalized[offset + i] = 0;
                }
            }

            // 最初のフレームで最初の数チャンネルのみログ出力
            if (ch < 3) {
                let minVal = Infinity, maxVal = -Infinity;
                for (let i = 0; i < spatialSize; i++) {
                    const v = normalized[offset + i];
                    if (v < minVal) minVal = v;
                    if (v > maxVal) maxVal = v;
                }
                console.log(`[GVRM] Normalized ch${ch}: mean=${mean.toFixed(2)}, std=${std.toFixed(2)}, range=[${minVal.toFixed(3)}, ${maxVal.toFixed(3)}]`);
            }
        }

        return normalized;
    }

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

            // Coarse Feature Mapの正規化（Neural Refinerは-1〜+1を期待）
            const normalizedCoarseFm = this.normalizeCoarseFeatureMap(coarseFm);

            // 正規化後の統計を確認
            let normMin = Infinity, normMax = -Infinity, normSum = 0;
            for (let i = 0; i < Math.min(normalizedCoarseFm.length, 10000); i++) {
                const v = normalizedCoarseFm[i];
                if (v < normMin) normMin = v;
                if (v > normMax) normMax = v;
                normSum += v;
            }
            console.log('[GVRM] Normalized coarse FM:', {
                min: normMin.toFixed(4),
                max: normMax.toFixed(4),
                mean: (normSum / 10000).toFixed(4)
            });

            const startTime = performance.now();
            const refinedRgb = await this.refiner.process(normalizedCoarseFm, this.idEmbedding);
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
