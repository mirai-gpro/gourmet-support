// src/gvrm-format/gs.ts
import * as THREE from 'three';

const vertexShader = `
    attribute vec4 latentTile; // 4ch単位の特徴量
    attribute vec4 boneIndices, boneWeights;
    uniform mat4 boneMatrices[64];
    uniform float pointSize;
    varying vec4 vFeature;

    void main() {
        mat4 skinMatrix = boneWeights.x * boneMatrices[int(boneIndices.x)] +
                         boneWeights.y * boneMatrices[int(boneIndices.y)] +
                         boneWeights.z * boneMatrices[int(boneIndices.z)] +
                         boneWeights.w * boneMatrices[int(boneIndices.w)];
        
        vec4 posedPos = skinMatrix * vec4(position, 1.0);
        vec4 mvPosition = modelViewMatrix * posedPos;
        gl_Position = projectionMatrix * mvPosition;
        
        // ポイントサイズを距離に応じて調整
        gl_PointSize = pointSize * (300.0 / -mvPosition.z);
        
        vFeature = latentTile;
    }
`;

const fragmentShader = `
    varying vec4 vFeature;
    void main() { 
        // 円形のポイントにする
        vec2 center = gl_PointCoord - vec2(0.5);
        if (length(center) > 0.5) discard;
        
        gl_FragColor = vFeature; 
    }
`;

export class GSViewer {
    public mesh: THREE.Points;
    private geometry: THREE.BufferGeometry;
    private latentData: Float32Array; // 32ch全データ
    private vertexCount: number;

    constructor(data: any) {
        this.vertexCount = data.vertexCount;
        this.latentData = data.latents;
        
        console.log('[GSViewer] Initializing...', {
            vertexCount: this.vertexCount,
            latentsLength: this.latentData.length,
            expectedLength: this.vertexCount * 32
        });
        
        this.geometry = new THREE.BufferGeometry();
        this.geometry.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
        this.geometry.setAttribute('boneIndices', new THREE.BufferAttribute(data.boneIndices, 4));
        this.geometry.setAttribute('boneWeights', new THREE.BufferAttribute(data.boneWeights, 4));
        
        // 初期状態として最初の4chをセット
        this.updateLatentTile(0);

        const material = new THREE.ShaderMaterial({
            vertexShader, fragmentShader,
            uniforms: { 
                boneMatrices: { value: new Float32Array(16 * 64) },
                pointSize: { value: 10.0 }  // より大きなポイントサイズ
            },
            depthTest: true, 
            transparent: false,  // 透明度をオフ（フルカバレッジ）
            depthWrite: true
        });

        this.mesh = new THREE.Points(this.geometry, material);
        this.mesh.frustumCulled = false;
        
        console.log('[GSViewer] ✅ Initialized successfully');
    }

    public updateLatentTile(tileIndex: number) {
        // 32chの中からi番目の4chセット(RGBA)を抽出して属性を更新
        // 
        // データレイアウト:
        // latentData = [v0_ch0, v0_ch1, ..., v0_ch31, v1_ch0, v1_ch1, ..., v1_ch31, ...]
        //              ^^^^^^^^ 頂点0の32ch ^^^^^^^^  ^^^^^^^^ 頂点1の32ch ^^^^^^^^
        //
        // Tile 0: ch0-3, Tile 1: ch4-7, ..., Tile 7: ch28-31
        
        if (tileIndex < 0 || tileIndex >= 8) {
            console.error(`[GSViewer] Invalid tileIndex: ${tileIndex}, must be 0-7`);
            return;
        }
        
        const tile = new Float32Array(this.vertexCount * 4);
        const startCh = tileIndex * 4; // 開始チャンネル: 0, 4, 8, ..., 28
        
        // デバッグ用の統計情報
        let minVal = Infinity;
        let maxVal = -Infinity;
        let nanCount = 0;
        let infCount = 0;
        let zeroCount = 0;
        const debugSamples: number[] = [];
        
        for (let i = 0; i < this.vertexCount; i++) {
            const baseIdx = i * 32; // この頂点の32chの開始位置
            
            for (let c = 0; c < 4; c++) {
                const srcIdx = baseIdx + startCh + c; // 実際のチャンネルインデックス
                const dstIdx = i * 4 + c;            // 出力配列のインデックス
                
                // 範囲チェック
                if (srcIdx >= this.latentData.length) {
                    console.error(`[GSViewer] Index out of bounds: srcIdx=${srcIdx}, length=${this.latentData.length}`);
                    tile[dstIdx] = 0;
                    continue;
                }
                
                let value = this.latentData[srcIdx];
                
                // NaN/Infinityチェック
                if (isNaN(value)) {
                    nanCount++;
                    value = 0;
                } else if (!isFinite(value)) {
                    infCount++;
                    value = 0;
                }
                
                if (value === 0) {
                    zeroCount++;
                }
                
                tile[dstIdx] = value;
                
                // 統計用
                if (isFinite(value)) {
                    minVal = Math.min(minVal, value);
                    maxVal = Math.max(maxVal, value);
                }
            }
            
            // 最初の10頂点のサンプルを記録
            if (i < 10) {
                debugSamples.push(tile[i * 4]); // R値のみ
            }
        }
        
        const totalValues = this.vertexCount * 4;
        const nonZeros = totalValues - zeroCount;
        
        console.log(`[GSViewer] Tile ${tileIndex} (ch${startCh}-${startCh+3}):`, {
            vertexCount: this.vertexCount,
            totalValues: totalValues,
            nonZeros: nonZeros,
            zeros: zeroCount,
            min: minVal === Infinity ? 0 : minVal.toFixed(4),
            max: maxVal === -Infinity ? 0 : maxVal.toFixed(4),
            nanCount: nanCount,
            infCount: infCount,
            samples: debugSamples.map(v => v.toFixed(3))
        });
        
        if (nanCount > 0 || infCount > 0) {
            console.warn(`[GSViewer] Tile ${tileIndex}: Cleaned ${nanCount} NaN and ${infCount} Infinity values`);
        }
        
        // 属性を更新
        this.geometry.setAttribute('latentTile', new THREE.BufferAttribute(tile, 4));
    }

    public updateBones(matrices: Float32Array) {
        (this.mesh.material as THREE.ShaderMaterial).uniforms.boneMatrices.value.set(matrices);
    }
}