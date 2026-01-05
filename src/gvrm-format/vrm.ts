import * as THREE from 'three';
export class VRMManager {
    public bones: Record<string, THREE.Object3D> = {};
    update() { /* ボーン行列の更新ロジック */ }
}
