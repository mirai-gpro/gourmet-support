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
        this.renderer.setSize(512, 512);  // 512×512 for coarse feature map
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

        this.renderTarget = new THREE.WebGLRenderTarget(512, 512, {  // 512×512
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
     * 🔍 Coarse Feature Map 診断
     * 空間構造・チャンネル分布・背景/前景分離を確認
     */
    private diagnoseCoarseFeatureMap(coarseFm: Float32Array): void {
        const numChannels = 32;
        const H = 512, W = 512;  // 512×512
        const spatialSize = H * W;

        console.log('[GVRM] ===== Coarse Feature Map 診断 (512×512) =====');

        // 1. 全体統計
        let totalMin = Infinity, totalMax = -Infinity;
        let zeroCount = 0, nearZeroCount = 0;
        for (let i = 0; i < coarseFm.length; i++) {
            const v = coarseFm[i];
            if (v < totalMin) totalMin = v;
            if (v > totalMax) totalMax = v;
            if (v === 0) zeroCount++;
            if (Math.abs(v) < 0.01) nearZeroCount++;
        }
        const zeroRatio = (zeroCount / coarseFm.length * 100).toFixed(1);
        const nearZeroRatio = (nearZeroCount / coarseFm.length * 100).toFixed(1);
        console.log(`[GVRM] 全体: min=${totalMin.toFixed(2)}, max=${totalMax.toFixed(2)}, ゼロ=${zeroRatio}%, ほぼゼロ=${nearZeroRatio}%`);

        // 2. 空間分布チェック（中央 vs 周辺）
        // 人物が中央にいれば、中央に値が集中するはず
        const centerRegion = { minY: 128, maxY: 384, minX: 128, maxX: 384 };  // 中央256x256
        let centerSum = 0, centerCount = 0;
        let edgeSum = 0, edgeCount = 0;

        for (let ch = 0; ch < numChannels; ch++) {
            const offset = ch * spatialSize;
            for (let y = 0; y < H; y++) {
                for (let x = 0; x < W; x++) {
                    const idx = offset + y * W + x;
                    const v = Math.abs(coarseFm[idx]);
                    if (y >= centerRegion.minY && y < centerRegion.maxY &&
                        x >= centerRegion.minX && x < centerRegion.maxX) {
                        centerSum += v;
                        centerCount++;
                    } else {
                        edgeSum += v;
                        edgeCount++;
                    }
                }
            }
        }
        const centerAvg = (centerSum / centerCount).toFixed(4);
        const edgeAvg = (edgeSum / edgeCount).toFixed(4);
        console.log(`[GVRM] 空間分布: 中央平均=${centerAvg}, 周辺平均=${edgeAvg}, 比率=${(centerSum/centerCount / (edgeSum/edgeCount)).toFixed(2)}x`);

        // 3. チャンネルごとの非ゼロ率
        const channelStats: string[] = [];
        for (let ch = 0; ch < Math.min(8, numChannels); ch++) {
            const offset = ch * spatialSize;
            let nonZero = 0;
            let chSum = 0;
            for (let i = 0; i < spatialSize; i++) {
                const v = coarseFm[offset + i];
                if (Math.abs(v) > 0.01) nonZero++;
                chSum += Math.abs(v);
            }
            const nonZeroRatio = (nonZero / spatialSize * 100).toFixed(0);
            const avgMag = (chSum / spatialSize).toFixed(2);
            channelStats.push(`Ch${ch}:${nonZeroRatio}%/${avgMag}`);
        }
        console.log(`[GVRM] チャンネル統計(非ゼロ率/平均絶対値): ${channelStats.join(', ')}`);

        // 4. 値のヒストグラム（概要）
        const bins = [0, 0, 0, 0, 0]; // <-100, -100~-1, -1~1, 1~100, >100
        for (let i = 0; i < coarseFm.length; i++) {
            const v = coarseFm[i];
            if (v < -100) bins[0]++;
            else if (v < -1) bins[1]++;
            else if (v <= 1) bins[2]++;
            else if (v <= 100) bins[3]++;
            else bins[4]++;
        }
        const total = coarseFm.length;
        console.log(`[GVRM] 値分布: <-100:${(bins[0]/total*100).toFixed(1)}%, -100~-1:${(bins[1]/total*100).toFixed(1)}%, -1~1:${(bins[2]/total*100).toFixed(1)}%, 1~100:${(bins[3]/total*100).toFixed(1)}%, >100:${(bins[4]/total*100).toFixed(1)}%`);

        console.log('[GVRM] =====================================');
    }

    /**
     * Coarse Feature Mapの正規化
     * 方式: パーセンタイルベース正規化（外れ値を除外）
     * 1st〜99thパーセンタイルで正規化し、外れ値はクリップ
     */
    private normalizeCoarseFeatureMap(coarseFm: Float32Array): Float32Array {
        const numChannels = 32;
        const spatialSize = 512 * 512;  // 512×512
        const normalized = new Float32Array(coarseFm.length);

        // チャンネルごとに正規化
        for (let ch = 0; ch < numChannels; ch++) {
            const offset = ch * spatialSize;

            // このチャンネルの非ゼロ値を収集
            const values: number[] = [];
            for (let i = 0; i < spatialSize; i++) {
                const val = coarseFm[offset + i];
                if (isFinite(val) && Math.abs(val) > 0.001) {
                    values.push(val);
                }
            }

            if (values.length < 100) {
                // ほぼ空のチャンネル → 0で埋める
                for (let i = 0; i < spatialSize; i++) {
                    normalized[offset + i] = 0;
                }
                continue;
            }

            // ソートしてパーセンタイルを取得
            values.sort((a, b) => a - b);
            const p1 = values[Math.floor(values.length * 0.01)];  // 1st percentile
            const p99 = values[Math.floor(values.length * 0.99)]; // 99th percentile

            const pRange = p99 - p1;
            if (pRange < 0.001) {
                for (let i = 0; i < spatialSize; i++) {
                    normalized[offset + i] = 0;
                }
                continue;
            }

            // パーセンタイル範囲で正規化、外れ値はクリップ
            for (let i = 0; i < spatialSize; i++) {
                const val = coarseFm[offset + i];
                if (!isFinite(val) || Math.abs(val) < 0.001) {
                    // 背景（ゼロ付近）は0にマップ
                    normalized[offset + i] = 0;
                } else {
                    // [p1, p99] → [-1, 1]、範囲外はクリップ
                    let norm = ((val - p1) / pRange) * 2 - 1;
                    normalized[offset + i] = Math.max(-1, Math.min(1, norm));
                }
            }

            // 最初の3チャンネルのみログ出力
            if (ch < 3) {
                console.log(`[GVRM] Ch${ch} p1=${p1.toFixed(1)}, p99=${p99.toFixed(1)}, nonZero=${values.length}`);
            }
        }

        // 正規化後の全体統計
        let normMin = Infinity, normMax = -Infinity;
        let zeroCount = 0;
        for (let i = 0; i < normalized.length; i++) {
            const v = normalized[i];
            if (v < normMin) normMin = v;
            if (v > normMax) normMax = v;
            if (v === 0) zeroCount++;
        }
        const zeroRatio = (zeroCount / normalized.length * 100).toFixed(1);
        console.log(`[GVRM] Normalized: range=[${normMin.toFixed(4)}, ${normMax.toFixed(4)}], zeros=${zeroRatio}%`);

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

        const FM_SIZE = 512;  // 512×512 coarse feature map
        const coarseFm = new Float32Array(1 * 32 * FM_SIZE * FM_SIZE);

        if (this.frameCount === 1) {
            console.log(`[GVRM] Generating coarse feature map (8 passes, ${FM_SIZE}×${FM_SIZE})...`);
        }

        for (let i = 0; i < 8; i++) {
            this.viewer.updateLatentTile(i);
            this.renderer.setRenderTarget(this.renderTarget);
            this.renderer.render(this.scene, this.camera);

            const pixels = new Float32Array(FM_SIZE * FM_SIZE * 4);
            this.renderer.readRenderTargetPixels(this.renderTarget, 0, 0, FM_SIZE, FM_SIZE, pixels);

            if (this.frameCount === 1 && i === 0) {
                // weighted average 前の生値
                let rawMin = Infinity, rawMax = -Infinity;
                let alphaMin = Infinity, alphaMax = -Infinity;
                for (let j = 0; j < 1000; j++) {
                    const r = pixels[j * 4];
                    const a = pixels[j * 4 + 3];
                    if (r < rawMin) rawMin = r;
                    if (r > rawMax) rawMax = r;
                    if (a > 0) {
                        if (a < alphaMin) alphaMin = a;
                        if (a > alphaMax) alphaMax = a;
                    }
                }
                console.log(`[GVRM] Pass ${i} raw:`, {
                    featureRange: `[${rawMin.toFixed(1)}, ${rawMax.toFixed(1)}]`,
                    alphaRange: `[${alphaMin.toFixed(3)}, ${alphaMax.toFixed(3)}]`
                });
            }

            const spatialSize = FM_SIZE * FM_SIZE;
            const baseOffset = i * 4 * spatialSize;

            // Weighted average: feature = Σ(f × α) / Σ(α)
            // シェーダー出力: RGB = f0,f1,f2 × α, A = Σ(α)
            for (let p = 0; p < spatialSize; p++) {
                const fTimesAlpha0 = pixels[p * 4 + 0];
                const fTimesAlpha1 = pixels[p * 4 + 1];
                const fTimesAlpha2 = pixels[p * 4 + 2];
                const alphaSum = pixels[p * 4 + 3];

                // αで割って weighted average を計算
                // α=0 の場合（背景）は 0 のまま
                if (alphaSum > 0.001) {
                    coarseFm[baseOffset + p] = fTimesAlpha0 / alphaSum;
                    coarseFm[baseOffset + spatialSize + p] = fTimesAlpha1 / alphaSum;
                    coarseFm[baseOffset + spatialSize * 2 + p] = fTimesAlpha2 / alphaSum;
                } else {
                    coarseFm[baseOffset + p] = 0;
                    coarseFm[baseOffset + spatialSize + p] = 0;
                    coarseFm[baseOffset + spatialSize * 2 + p] = 0;
                }

                // ⚠️ ch3, ch7, ch11, ... は現在のシェーダーでは取得不可
                // 暫定: 隣接チャンネルの平均で補間
                const ch3Value = (coarseFm[baseOffset + p] +
                                  coarseFm[baseOffset + spatialSize + p] +
                                  coarseFm[baseOffset + spatialSize * 2 + p]) / 3;
                coarseFm[baseOffset + spatialSize * 3 + p] = ch3Value;
            }
        }

        if (!this.isFirstFrameProcessed) {
            console.log('[GVRM] Calling Neural Refiner...');

            // 🔍 Coarse Feature Map 診断
            this.diagnoseCoarseFeatureMap(coarseFm);

            // Coarse Feature Mapの正規化（Neural Refinerは-1〜+1を期待）
            const normalizedCoarseFm = this.normalizeCoarseFeatureMap(coarseFm);

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
