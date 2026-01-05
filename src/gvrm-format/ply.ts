export class PLYLoader {
    static async load(url: string) {
        console.log("[GVRM] PLYLoader: Start Fetching " + url);
        const res = await fetch(url);
        const buffer = await res.arrayBuffer();
        const headerText = new TextDecoder().decode(buffer.slice(0, 5000));
        const headerEndIdx = headerText.indexOf("end_header") + 10;
        const vertexCount = parseInt(headerText.match(/element vertex (\d+)/)![1]);
        const props = headerText.split('\n').filter(l => l.startsWith('property float')).map(l => l.split(' ').pop()?.trim() || "");
        const stride = props.length * 4;
        const dataView = new DataView(buffer, headerEndIdx + 1);

        const data = {
            positions: new Float32Array(vertexCount * 3),
            colors: new Float32Array(vertexCount * 3),
            opacities: new Float32Array(vertexCount),
            scales: new Float32Array(vertexCount * 3),
            rots: new Float32Array(vertexCount * 4),
            boneIndices: new Float32Array(vertexCount * 4),
            boneWeights: new Float32Array(vertexCount * 4)
        };

        for (let i = 0; i < vertexCount; i++) {
            const b = i * stride;
            const read = (n: string) => {
                const idx = props.indexOf(n);
                return idx === -1 ? 0 : dataView.getFloat32(b + idx * 4, true);
            };
            data.positions.set([read('x'), read('y'), read('z')], i * 3);
            data.colors.set([read('f_dc_0'), read('f_dc_1'), read('f_dc_2')], i * 3);
            data.opacities[i] = read('opacity');
            data.scales.set([read('scale_0'), read('scale_1'), read('scale_2')], i * 3);
            data.rots.set([read('rot_0'), read('rot_1'), read('rot_2'), read('rot_3')], i * 4);
            data.boneIndices.set([read('bone_index_0'), read('bone_index_1'), read('bone_index_2'), read('bone_index_3')], i * 4);
            data.boneWeights.set([read('bone_weight_0'), read('bone_weight_1'), read('bone_weight_2'), read('bone_weight_3')], i * 4);
        }
        console.log("[GVRM] PLY Data Ready. Points:", vertexCount);
        return data;
    }
}
