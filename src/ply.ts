$content = @'
import * as THREE from 'three';

// テンプレート（理想の点）の型定義
type TemplatePoint = { x: number, y: number, z: number, bone: number };

export class PLYLoader {
    static async load(url: string) {
        console.log("[GVRM] PLYLoader: Start Fetching " + url);
        const res = await fetch(url);
        const buffer = await res.arrayBuffer();
        
        // ヘッダー解析
        const headerText = new TextDecoder().decode(buffer.slice(0, 5000));
        const headerEndIdx = headerText.indexOf("end_header") + 10;
        const vertexCount = parseInt(headerText.match(/element vertex (\d+)/)![1]);
        const props = headerText.split('\n').filter(l => l.startsWith('property float')).map(l => l.split(' ').pop()?.trim() || "");
        const stride = props.length * 4;
        const dataView = new DataView(buffer, headerEndIdx + 1);

        const data = {
            positions: new Float32Array(vertexCount * 3),
            colors: new Float32Array(vertexCount * 3),
            boneIndices: new Float32Array(vertexCount * 4),
            boneWeights: new Float32Array(vertexCount * 4),
            scales: new Float32Array(vertexCount * 3)
        };

        // --- Pass 1: 重心と高さの測定 (正規化用) ---
        let minY = Infinity, maxY = -Infinity;
        let sumX = 0, sumZ = 0;

        for (let i = 0; i < vertexCount; i++) {
            const b = i * stride;
            const x = dataView.getFloat32(b + props.indexOf('x') * 4, true);
            const y = dataView.getFloat32(b + props.indexOf('y') * 4, true);
            const z = dataView.getFloat32(b + props.indexOf('z') * 4, true);

            if (y < minY) minY = y; 
            if (y > maxY) maxY = y;
            sumX += x;
            sumZ += z;
        }

        const centerX = sumX / vertexCount;
        const centerZ = sumZ / vertexCount;
        const rawHeight = maxY - minY;
        const scaleFactor = 1.70 / rawHeight; // 身長1.7mに強制変換

        console.log(`[GVRM] Normalizing... Height: ${rawHeight.toFixed(2)}m -> 1.70m`);

        // --- 仮想テンプレートの生成 (案Aの中核) ---
        // 理想的な人体の表面に「正解の点」を配置します
        const templatePoints: TemplatePoint[] = [];

        // ヘルパー: 線分上に点をばら撒く (太さradius付き)
        const addBoneSegment = (boneIdx: number, p1: number[], p2: number[], radius: number, count: number) => {
            for (let i = 0; i < count; i++) {
                const t = i / (count - 1);
                // 線形補間
                const cx = p1[0] + (p2[0] - p1[0]) * t;
                const cy = p1[1] + (p2[1] - p1[1]) * t;
                const cz = p1[2] + (p2[2] - p1[2]) * t;
                
                // 円周上に配置 (簡易的なボリューム感)
                for (let j = 0; j < 4; j++) { // 前後左右
                    const angle = (j / 4) * Math.PI * 2;
                    templatePoints.push({
                        x: cx + Math.cos(angle) * radius,
                        y: cy,
                        z: cz + Math.sin(angle) * radius,
                        bone: boneIdx
                    });
                }
                // 中心点も追加
                templatePoints.push({ x: cx, y: cy, z: cz, bone: boneIdx });
            }
        };

        // === テンプレート定義 (Aポーズ) ===
        // 1. 体幹 (Spine)
        addBoneSegment(0, [0, 0.9, 0], [0, 1.1, 0], 0.12, 10); // Hips
        addBoneSegment(3, [0, 1.1, 0], [0, 1.3, 0], 0.14, 10); // Spine1
        addBoneSegment(9, [0, 1.3, 0], [0, 1.5, 0], 0.15, 10); // Chest

        // 2. 首と頭 (Neck, Head)
        addBoneSegment(12, [0, 1.5, 0], [0, 1.6, 0], 0.06, 5); // Neck
        addBoneSegment(15, [0, 1.6, 0], [0, 1.85, 0], 0.10, 15); // Head (球体っぽく)
        
        // 3. 顎 (Jaw) - 鼻の下あたりに集中的に配置
        // 範囲を極限まで限定 (前髪誤爆防止)
        addBoneSegment(22, [0, 1.52, 0.07], [0, 1.56, 0.07], 0.03, 8); 

        // 4. 腕 (Arm) - Aポーズ (斜め下)
        // 肩(0.2, 1.45) -> 手(0.5, 1.1)
        addBoneSegment(16, [0.22, 1.42, 0], [0.55, 1.0, 0.1], 0.06, 20); // Left Arm
        addBoneSegment(17, [-0.22, 1.42, 0], [-0.55, 1.0, 0.1], 0.06, 20); // Right Arm

        console.log(`[GVRM] Template generated. Points: ${templatePoints.length}`);


        // --- Pass 2: 最近傍探索 (Nearest Neighbor) ---
        let jawCount = 0;

        for (let i = 0; i < vertexCount; i++) {
            const b = i * stride;
            const read = (n: string) => {
                const idx = props.indexOf(n);
                return idx === -1 ? 0 : dataView.getFloat32(b + idx * 4, true);
            };

            let x = read('x');
            let y = read('y');
            let z = read('z');

            // 1. 正規化 (テンプレートと同じ空間に合わせる)
            x = (x - centerX) * scaleFactor;
            y = (y - minY) * scaleFactor;
            z = (z - centerZ) * scaleFactor;

            // データの書き戻し
            data.positions.set([x, y, z], i * 3);
            data.colors.set([read('f_dc_0'), read('f_dc_1'), read('f_dc_2')], i * 3);
            data.scales.set([read('scale_0')*scaleFactor, read('scale_1')*scaleFactor, read('scale_2')*scaleFactor], i * 3);

            // 2. テンプレートマッチング (一番近い理想点を探す)
            let bestBone = 0;
            let minDistSq = Infinity;

            // 高速化のため、Y座標の差が大きすぎる点はスキップ
            for (let j = 0; j < templatePoints.length; j++) {
                const tp = templatePoints[j];
                const dy = y - tp.y;
                if (Math.abs(dy) > 0.3) continue; // 30cm以上離れていたら計算しない

                const distSq = (x - tp.x)**2 + dy**2 + (z - tp.z)**2;
                if (distSq < minDistSq) {
                    minDistSq = distSq;
                    bestBone = tp.bone;
                }
            }
            
            // 顎判定の統計
            if (bestBone === 22) jawCount++;

            data.boneIndices.set([bestBone, 0, 0, 0], i * 4);
            data.boneWeights.set([1.0, 0.0, 0.0, 0.0], i * 4);
        }
        
        console.log(`[GVRM] Rigging Complete (Template Matching). Jaw points: ${jawCount}`);
        return data;
    }
}
'@
Set-Content -Path "src/gvrm-format/ply.ts" -Value $content -Encoding UTF8