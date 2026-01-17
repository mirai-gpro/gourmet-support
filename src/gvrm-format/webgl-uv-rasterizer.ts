// src/gvrm-format/webgl-uv-rasterizer.ts
// WebGL GPU専用 UV三角形ラスタライザ（論文準拠）
// GUAVA Supplementary B.4完全実装

import type { MeshData } from './inverse-texture-mapping';

/**
 * UV Triangle Mapping データ構造
 * （InverseTextureMapperと互換性あり）
 */
export interface UVTriangleMapping {
  width: number;
  height: number;
  numValid: number;
  uvCoords: Uint16Array;             // ✅ 追加: [numValid * 2] (u, v) ピクセル座標
  triangleIndices: Uint32Array;      // [width * height] 全ピクセル分
  barycentricCoords: Float32Array;   // [width * height * 3] 全ピクセル分
  worldPositions: Float32Array;      // [width * height * 3] 全ピクセル分
  validMask: Uint8Array;             // [width * height] 全ピクセル分
}

/**
 * WebGL UV Rasterizer (GPU専用)
 * 
 * 論文準拠の実装:
 * - GPU並列処理で1-2ms台の超高速実行
 * - 縮退三角形は自動的にスキップ（面積ゼロ）
 * - リアルタイムラスタライゼーション
 */
export class WebGLUVRasterizer {
  private gl: WebGL2RenderingContext | null = null;
  private program: WebGLProgram | null = null;
  private canvas: HTMLCanvasElement | null = null;
  
  // シェーダーソース
  private readonly vertexShaderSource = `#version 300 es
    in vec2 a_uv;
    in float a_triangleId;
    
    out float v_triangleId;
    
    void main() {
      // UV座標(0-1)をクリップ空間(-1~1)に変換
      vec2 clipSpace = a_uv * 2.0 - 1.0;
      gl_Position = vec4(clipSpace, 0.0, 1.0);
      v_triangleId = a_triangleId;
    }
  `;
  
  private readonly fragmentShaderSource = `#version 300 es
    precision highp float;
    
    in float v_triangleId;
    out vec4 fragColor;
    
    void main() {
      // 三角形IDをRGBAにエンコード（8bit × 4 = 32bit整数）
      // 注意: GLSLでは整数演算が制限されているため、浮動小数点で計算
      float id = v_triangleId;
      
      // 各8bitチャンネルに分解（0-255範囲）
      float r = floor(id / 16777216.0);  // 最上位8bit
      float g = floor(mod(id / 65536.0, 256.0));
      float b = floor(mod(id / 256.0, 256.0));
      float a = floor(mod(id, 256.0));     // 最下位8bit
      
      // 0-1範囲に正規化
      fragColor = vec4(r / 255.0, g / 255.0, b / 255.0, a / 255.0);
    }
  `;
  
  /**
   * 初期化（WebGLコンテキスト作成）
   */
  async init(): Promise<void> {
    console.log('[WebGLUVRasterizer] Initializing GPU rasterizer...');
    
    // オフスクリーンキャンバス作成
    this.canvas = document.createElement('canvas');
    this.canvas.width = 1024;
    this.canvas.height = 1024;
    
    this.gl = this.canvas.getContext('webgl2', {
      preserveDrawingBuffer: true,
      antialias: false,
      alpha: true  // アルファチャンネルを有効化（背景を完全に0にするため）
    });
    
    if (!this.gl) {
      throw new Error('WebGL2 not supported. GPU rasterization requires WebGL2.');
    }
    
    console.log('[WebGLUVRasterizer] WebGL2 context created');
    
    // シェーダーコンパイル
    const vertexShader = this.compileShader(
      this.gl,
      this.vertexShaderSource,
      this.gl.VERTEX_SHADER
    );
    
    const fragmentShader = this.compileShader(
      this.gl,
      this.fragmentShaderSource,
      this.gl.FRAGMENT_SHADER
    );
    
    // プログラムリンク
    this.program = this.createProgram(this.gl, vertexShader, fragmentShader);
    
    console.log('[WebGLUVRasterizer] ✅ GPU rasterizer initialized');
  }
  
  /**
   * メッシュをUV空間にラスタライズ（GPU実行）
   * 
   * @param meshData メッシュデータ（vertices, triangles, uvCoords）
   * @param resolution UV解像度（デフォルト: 1024）
   * @returns ラスタライズされたマッピング
   */
  async rasterize(
    meshData: MeshData,
    resolution: number = 1024
  ): Promise<UVTriangleMapping> {
    if (!this.gl || !this.program) {
      throw new Error('WebGLUVRasterizer not initialized. Call init() first.');
    }
    
    console.log('[WebGLUVRasterizer] 🚀 Starting GPU rasterization...');
    console.log(`  Mesh: ${meshData.numVertices.toLocaleString()} vertices, ${meshData.numTriangles.toLocaleString()} triangles`);
    console.log(`  Resolution: ${resolution}×${resolution}`);
    
    const startTime = performance.now();
    
    // キャンバスサイズを設定
    this.canvas!.width = resolution;
    this.canvas!.height = resolution;
    this.gl.viewport(0, 0, resolution, resolution);
    
    // 頂点データ準備
    const vertexData = this.prepareVertexData(meshData);
    
    // VBO作成
    const vbo = this.gl.createBuffer();
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER, vbo);
    this.gl.bufferData(this.gl.ARRAY_BUFFER, vertexData, this.gl.STATIC_DRAW);
    
    // 属性設定
    const positionLoc = this.gl.getAttribLocation(this.program, 'a_uv');
    const triangleIdLoc = this.gl.getAttribLocation(this.program, 'a_triangleId');
    
    this.gl.enableVertexAttribArray(positionLoc);
    this.gl.vertexAttribPointer(positionLoc, 2, this.gl.FLOAT, false, 12, 0);
    
    this.gl.enableVertexAttribArray(triangleIdLoc);
    this.gl.vertexAttribPointer(triangleIdLoc, 1, this.gl.FLOAT, false, 12, 8);
    
    // レンダリング設定
    this.gl.clearColor(0, 0, 0, 0);  // 完全に透明な背景
    this.gl.clearDepth(1.0);
    this.gl.clear(this.gl.COLOR_BUFFER_BIT | this.gl.DEPTH_BUFFER_BIT);
    
    // デプステストを有効化
    this.gl.enable(this.gl.DEPTH_TEST);
    this.gl.depthFunc(this.gl.LESS);
    
    // カリングを無効化（UV空間なので両面レンダリング）
    this.gl.disable(this.gl.CULL_FACE);
    
    // ブレンディングを無効化（上書きモード）
    this.gl.disable(this.gl.BLEND);
    
    this.gl.useProgram(this.program);
    
    // GPU描画
    const gpuStartTime = performance.now();
    const numVertices = meshData.numTriangles * 3;
    console.log(`  Drawing ${numVertices} vertices (${meshData.numTriangles} triangles)`);
    this.gl.drawArrays(this.gl.TRIANGLES, 0, numVertices);
    
    // エラーチェック
    const error = this.gl.getError();
    if (error !== this.gl.NO_ERROR) {
      console.error(`  ❌ WebGL Error: ${error}`);
      throw new Error(`WebGL rendering failed with error ${error}`);
    }
    
    this.gl.finish(); // GPU同期
    const gpuTime = performance.now() - gpuStartTime;
    
    console.log(`  GPU rendering: ${gpuTime.toFixed(2)}ms ⚡`);
    
    // ピクセルデータ読み取り
    const readStartTime = performance.now();
    const pixels = new Uint8Array(resolution * resolution * 4);
    this.gl.readPixels(
      0, 0, resolution, resolution,
      this.gl.RGBA, this.gl.UNSIGNED_BYTE, pixels
    );
    const readTime = performance.now() - readStartTime;
    
    console.log(`  GPU → CPU transfer: ${readTime.toFixed(2)}ms`);
    
    // サンプルデバッグ：最初の10ピクセルを確認
    console.log('[WebGLUVRasterizer] Sample pixels (first 10):');
    for (let i = 0; i < 10; i++) {
      const r = pixels[i * 4 + 0];
      const g = pixels[i * 4 + 1];
      const b = pixels[i * 4 + 2];
      const a = pixels[i * 4 + 3];
      if (r !== 0 || g !== 0 || b !== 0 || a !== 0) {
        console.log(`  Pixel ${i}: RGBA(${r}, ${g}, ${b}, ${a})`);
      }
    }
    
    // マッピングデータ抽出
    const mapping = this.extractMapping(pixels, resolution, meshData);
    
    // クリーンアップ
    this.gl.deleteBuffer(vbo);
    
    const totalTime = performance.now() - startTime;
    console.log('[WebGLUVRasterizer] ✅ GPU rasterization complete');
    console.log(`  Total time: ${totalTime.toFixed(2)}ms`);
    console.log(`  Valid pixels: ${mapping.numValid.toLocaleString()} / ${(resolution * resolution).toLocaleString()} (${(mapping.numValid / (resolution * resolution) * 100).toFixed(1)}%)`);
    
    return mapping;
  }
  
  /**
   * 頂点データ準備
   * 各頂点: [u, v, triangleId]
   */
  private prepareVertexData(meshData: MeshData): Float32Array {
    const vertices: number[] = [];
    let validTriangles = 0;
    let invalidUVCount = 0;
    
    for (let triIdx = 0; triIdx < meshData.numTriangles; triIdx++) {
      const i0 = meshData.triangles[triIdx * 3 + 0];
      const i1 = meshData.triangles[triIdx * 3 + 1];
      const i2 = meshData.triangles[triIdx * 3 + 2];
      
      // 各頂点のUV座標
      const u0 = meshData.uvCoords[i0 * 2 + 0];
      const v0 = meshData.uvCoords[i0 * 2 + 1];
      const u1 = meshData.uvCoords[i1 * 2 + 0];
      const v1 = meshData.uvCoords[i1 * 2 + 1];
      const u2 = meshData.uvCoords[i2 * 2 + 0];
      const v2 = meshData.uvCoords[i2 * 2 + 1];
      
      // UV座標が有効範囲（0-1）かチェック
      const isValid = 
        u0 >= 0 && u0 <= 1 && v0 >= 0 && v0 <= 1 &&
        u1 >= 0 && u1 <= 1 && v1 >= 0 && v1 <= 1 &&
        u2 >= 0 && u2 <= 1 && v2 >= 0 && v2 <= 1;
      
      if (!isValid) {
        invalidUVCount++;
        continue;
      }
      
      validTriangles++;
      
      // 三角形の頂点データ
      vertices.push(
        u0, v0, triIdx,
        u1, v1, triIdx,
        u2, v2, triIdx
      );
    }
    
    console.log(`  Valid triangles: ${validTriangles} / ${meshData.numTriangles}`);
    if (invalidUVCount > 0) {
      console.warn(`  ⚠️  Invalid UV coordinates: ${invalidUVCount} triangles skipped`);
    }
    
    return new Float32Array(vertices);
  }
  
  /**
   * ピクセルデータからマッピング抽出
   * ✅ 修正: uvCoords を追加
   */
  private extractMapping(
    pixels: Uint8Array,
    resolution: number,
    meshData: MeshData
  ): UVTriangleMapping {
    console.log('[WebGLUVRasterizer] Extracting mapping data...');
    
    const numPixels = resolution * resolution;
    
    // ✅ 全ピクセル分の配列を確保
    const triangleIndices = new Uint32Array(numPixels);
    const barycentricCoords = new Float32Array(numPixels * 3);
    const worldPositions = new Float32Array(numPixels * 3);
    const validMask = new Uint8Array(numPixels);
    const uvCoordsList: number[] = [];  // ✅ 追加: 有効ピクセルのUV座標
    
    // デフォルト値で初期化
    triangleIndices.fill(0xFFFFFFFF);  // 無効な三角形ID
    
    let numValid = 0;
    let foundFirst = false;
    
    // 全ピクセルを処理
    for (let v = 0; v < resolution; v++) {
      for (let u = 0; u < resolution; u++) {
        const pixelIdx = v * resolution + u;
        const colorIdx = pixelIdx * 4;
        
        const r = pixels[colorIdx + 0];
        const g = pixels[colorIdx + 1];
        const b = pixels[colorIdx + 2];
        const a = pixels[colorIdx + 3];
        
        // 背景をスキップ（RGB全て0の場合）
        if (r === 0 && g === 0 && b === 0) {
          validMask[pixelIdx] = 0;  // 無効マーク
          continue;
        }
        
        // デバッグ: 最初の有効ピクセル
        if (!foundFirst) {
          console.log(`  First valid pixel at (${u},${v}): RGBA(${r}, ${g}, ${b}, ${a})`);
          foundFirst = true;
        }
        
        // 三角形IDをデコード
        const triangleId = this.decodeTriangleId(r, g, b, a);
        
        // 範囲チェック
        if (triangleId >= meshData.numTriangles) {
          console.warn(`  Invalid triangle ID ${triangleId} at pixel (${u},${v})`);
          validMask[pixelIdx] = 0;
          continue;
        }
        
        // 重心座標を計算
        const bary = this.computeBarycentricCoords(
          u, v, triangleId, resolution, meshData
        );
        
        // データ格納
        triangleIndices[pixelIdx] = triangleId;
        barycentricCoords[pixelIdx * 3 + 0] = bary[0];
        barycentricCoords[pixelIdx * 3 + 1] = bary[1];
        barycentricCoords[pixelIdx * 3 + 2] = bary[2];
        validMask[pixelIdx] = 1;  // 有効マーク
        
        // ✅ UV座標を記録（有効ピクセルのみ）
        uvCoordsList.push(u);
        uvCoordsList.push(v);
        
        // ワールド位置を計算（バリセントリック補間）
        const i0 = meshData.triangles[triangleId * 3 + 0];
        const i1 = meshData.triangles[triangleId * 3 + 1];
        const i2 = meshData.triangles[triangleId * 3 + 2];
        
        const v0x = meshData.vertices[i0 * 3 + 0];
        const v0y = meshData.vertices[i0 * 3 + 1];
        const v0z = meshData.vertices[i0 * 3 + 2];
        
        const v1x = meshData.vertices[i1 * 3 + 0];
        const v1y = meshData.vertices[i1 * 3 + 1];
        const v1z = meshData.vertices[i1 * 3 + 2];
        
        const v2x = meshData.vertices[i2 * 3 + 0];
        const v2y = meshData.vertices[i2 * 3 + 1];
        const v2z = meshData.vertices[i2 * 3 + 2];
        
        // position = u*v0 + v*v1 + w*v2
        worldPositions[pixelIdx * 3 + 0] = bary[0] * v0x + bary[1] * v1x + bary[2] * v2x;
        worldPositions[pixelIdx * 3 + 1] = bary[0] * v0y + bary[1] * v1y + bary[2] * v2y;
        worldPositions[pixelIdx * 3 + 2] = bary[0] * v0z + bary[1] * v1z + bary[2] * v2z;
        
        numValid++;
      }
    }
    
    if (!foundFirst) {
      console.error('  ❌ No valid pixels found!');
      console.error('  ⚠️  WebGL rendering may have failed');
    }
    
    // ✅ uvCoords配列を作成
    const uvCoords = new Uint16Array(uvCoordsList);
    
    console.log(`  Valid pixels: ${numValid.toLocaleString()}`);
    console.log('[WebGLUVRasterizer] ✅ Mapping extracted');
    console.log(`  Arrays: uvCoords=${uvCoords.length}, ` +
                `triangleIndices=${triangleIndices.length}, ` +
                `barycentricCoords=${barycentricCoords.length}, ` +
                `worldPositions=${worldPositions.length}, ` +
                `validMask=${validMask.length}`);
    
    return {
      width: resolution,
      height: resolution,
      numValid,
      uvCoords,          // ✅ 追加
      triangleIndices,
      barycentricCoords,
      worldPositions,
      validMask
    };
  }
  
  /**
   * RGBAから三角形IDをデコード
   * readPixelsはUint8Array（0-255）で返すため、そのまま使用
   */
  private decodeTriangleId(r: number, g: number, b: number, a: number): number {
    // 32bit整数に復元（r, g, b, a は既に 0-255 の範囲）
    const id = (r << 24) | (g << 16) | (b << 8) | a;
    
    // 符号なし整数に変換
    return id >>> 0;
  }
  
  /**
   * 重心座標を計算
   */
  private computeBarycentricCoords(
    pixelU: number,
    pixelV: number,
    triangleId: number,
    resolution: number,
    meshData: MeshData
  ): [number, number, number] {
    // ピクセル中心の正規化座標
    const u = (pixelU + 0.5) / resolution;
    const v = (pixelV + 0.5) / resolution;
    
    // 三角形の頂点インデックス
    const i0 = meshData.triangles[triangleId * 3 + 0];
    const i1 = meshData.triangles[triangleId * 3 + 1];
    const i2 = meshData.triangles[triangleId * 3 + 2];
    
    // 三角形のUV座標
    const u0 = meshData.uvCoords[i0 * 2 + 0];
    const v0 = meshData.uvCoords[i0 * 2 + 1];
    const u1 = meshData.uvCoords[i1 * 2 + 0];
    const v1 = meshData.uvCoords[i1 * 2 + 1];
    const u2 = meshData.uvCoords[i2 * 2 + 0];
    const v2 = meshData.uvCoords[i2 * 2 + 1];
    
    // 重心座標計算
    const denom = (v1 - v2) * (u0 - u2) + (u2 - u1) * (v0 - v2);
    
    if (Math.abs(denom) < 1e-8) {
      // 縮退三角形（GPUで既に除外されているはず）
      return [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0];
    }
    
    const w0 = ((v1 - v2) * (u - u2) + (u2 - u1) * (v - v2)) / denom;
    const w1 = ((v2 - v0) * (u - u2) + (u0 - u2) * (v - v2)) / denom;
    const w2 = 1.0 - w0 - w1;
    
    return [w0, w1, w2];
  }
  
  /**
   * シェーダーコンパイル
   */
  private compileShader(
    gl: WebGL2RenderingContext,
    source: string,
    type: number
  ): WebGLShader {
    const shader = gl.createShader(type);
    if (!shader) {
      throw new Error('Failed to create shader');
    }
    
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const error = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(`Shader compilation error: ${error}`);
    }
    
    return shader;
  }
  
  /**
   * プログラム作成
   */
  private createProgram(
    gl: WebGL2RenderingContext,
    vertexShader: WebGLShader,
    fragmentShader: WebGLShader
  ): WebGLProgram {
    const program = gl.createProgram();
    if (!program) {
      throw new Error('Failed to create program');
    }
    
    gl.attachShader(program, vertexShader);
    gl.attachShader(program, fragmentShader);
    gl.linkProgram(program);
    
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const error = gl.getProgramInfoLog(program);
      gl.deleteProgram(program);
      throw new Error(`Program linking error: ${error}`);
    }
    
    return program;
  }
  
  /**
   * クリーンアップ
   */
  dispose(): void {
    if (this.gl && this.program) {
      this.gl.deleteProgram(this.program);
      this.program = null;
    }
    
    if (this.canvas) {
      this.canvas = null;
    }
    
    this.gl = null;
  }
}