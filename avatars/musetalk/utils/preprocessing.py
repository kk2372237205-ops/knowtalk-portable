import sys
try:
    from face_detection import FaceAlignment,LandmarksType
except ModuleNotFoundError:  # 以 avatars.musetalk.utils.preprocessing 方式导入时本目录不在 sys.path
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    # 临时插入，导入完立刻撤掉：本目录里有 utils.py，长期留在 sys.path 会遮蔽
    # 项目的 utils 包，导致 `from utils.logger import ...` 报 "utils is not a package"。
    _added = _here not in sys.path
    if _added:
        sys.path.insert(0, _here)
    try:
        from face_detection import FaceAlignment,LandmarksType
    finally:
        if _added:
            try:
                sys.path.remove(_here)
            except ValueError:
                pass
from os import listdir, path
import subprocess
import numpy as np
import cv2
import pickle
import os
import json
try:
    from mmpose.apis import inference_topdown, init_model
    from mmpose.structures import merge_data_samples
    _MMPOSE_AVAILABLE = True
except Exception:  # mmpose / mmcv 未安装（Windows 上极难装）：回退 face_alignment
    _MMPOSE_AVAILABLE = False
import torch
from tqdm import tqdm

# initialize the mmpose model
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dwpose', 'rtmpose-l_8xb32-270e_coco-ubody-wholebody-384x288.py')
checkpoint_file = './models/dwpose/dw-ll_ucoco_384.pth'
model = init_model(config_file, checkpoint_file, device=device) if _MMPOSE_AVAILABLE else None

# initialize the face detection model
device = "cuda" if torch.cuda.is_available() else "cpu"
fa = FaceAlignment(LandmarksType._2D, flip_input=False,device=device)

# ---- 68 点面部关键点：mmpose/dwpose 优先，缺失时用 pip face_alignment(2DFAN-4) 等价替代 ----
# 上游只用 68 点里的面部点算「以鼻尖上下对称」的人脸框，两者点序同为 iBUG 68，可互换。
_fa_pip = None


def _find_jit_fan():
    """找 face_alignment 的 2DFAN4 TorchScript 权重（热力图版）。

    上游 adrianbulat 的 state_dict 版在国内基本拉不动；hf-mirror 上的
    2DFAN4-cd938726ad.zip 是 TorchScript 热力图模型，输出 (1,68,64,64)，
    正好是 face_alignment>=1.5 的 get_preds_fromhm 所期望的格式。
    """
    import glob
    ckpt = os.path.join(torch.hub.get_dir(), "checkpoints")
    for pat in ("2DFAN4-*.zip", "2DFAN4-*.pth.tar"):
        for p in sorted(glob.glob(os.path.join(ckpt, pat))):
            if os.path.getsize(p) > 50 * 1024 * 1024:
                return p
    return None


def _init_face_alignment():
    global _fa_pip
    if _fa_pip is None:
        import face_alignment as _fa_pkg
        _lm_type = getattr(_fa_pkg.LandmarksType, 'TWO_D', None)
        if _lm_type is None:
            _lm_type = _fa_pkg.LandmarksType._2D
        _dev = 'cuda' if torch.cuda.is_available() else 'cpu'
        _jit_path = _find_jit_fan()

        if _jit_path:
            class _OfflineFaceAlignment(_fa_pkg.FaceAlignment):
                """权重是 TorchScript 归档时，绕过 load_state_dict 直接 jit.load。"""

                def _load_native(self, network_name, network_size, device, dtype):
                    return torch.jit.load(_jit_path, map_location=device)

            _cls = _OfflineFaceAlignment
            print(f'[preprocessing] 68 点使用 TorchScript FAN: {_jit_path}', flush=True)
        else:
            _cls = _fa_pkg.FaceAlignment

        _fa_pip = _cls(
            _lm_type,
            device=_dev,
            face_detector='sfd',
            flip_input=False,
            verbose=False,
            compile=False,
        )
    return _fa_pip


def get_face_landmarks(frame):
    """返回单帧的 68 个面部关键点（iBUG 顺序，(x, y) 像素坐标）。"""
    if _MMPOSE_AVAILABLE and model is not None:
        results = inference_topdown(model, np.asarray(frame))
        results = merge_data_samples(results)
        keypoints = results.pred_instances.keypoints
        return keypoints[0][23:91]

    fa_pkg = _init_face_alignment()
    rgb = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_BGR2RGB)
    lms = fa_pkg.get_landmarks_from_image(rgb)
    if lms is None or len(lms) == 0:
        raise RuntimeError('未检测到人脸关键点，无法计算人脸框')
    return np.asarray(lms[0])


# maker if the bbox is not sufficient 
coord_placeholder = (0.0,0.0,0.0,0.0)

def resize_landmark(landmark, w, h, new_w, new_h):
    w_ratio = new_w / w
    h_ratio = new_h / h
    landmark_norm = landmark / [w, h]
    landmark_resized = landmark_norm * [new_w, new_h]
    return landmark_resized

def read_imgs(img_list):
    frames = []
    print('reading images...')
    for img_path in tqdm(img_list):
        frame = cv2.imread(img_path)
        frames.append(frame)
    return frames

def get_bbox_range(img_list,upperbondrange =0):
    frames = read_imgs(img_list)
    batch_size_fa = 1
    batches = [frames[i:i + batch_size_fa] for i in range(0, len(frames), batch_size_fa)]
    coords_list = []
    landmarks = []
    if upperbondrange != 0:
        print('get key_landmark and face bounding boxes with the bbox_shift:',upperbondrange)
    else:
        print('get key_landmark and face bounding boxes with the default value')
    average_range_minus = []
    average_range_plus = []
    for fb in tqdm(batches):
        face_land_mark = get_face_landmarks(np.asarray(fb)[0]).astype(np.int32)
        
        # get bounding boxes by face detetion
        bbox = fa.get_detections_for_batch(np.asarray(fb))
        
        # adjust the bounding box refer to landmark
        # Add the bounding box to a tuple and append it to the coordinates list
        for j, f in enumerate(bbox):
            if f is None: # no face in the image
                coords_list += [coord_placeholder]
                continue
            
            half_face_coord =  face_land_mark[29]#np.mean([face_land_mark[28], face_land_mark[29]], axis=0)
            range_minus = (face_land_mark[30]- face_land_mark[29])[1]
            range_plus = (face_land_mark[29]- face_land_mark[28])[1]
            average_range_minus.append(range_minus)
            average_range_plus.append(range_plus)
            if upperbondrange != 0:
                half_face_coord[1] = upperbondrange+half_face_coord[1] #手动调整  + 向下（偏29）  - 向上（偏28）

    text_range=f"Total frame:「{len(frames)}」 Manually adjust range : [ -{int(sum(average_range_minus) / len(average_range_minus))}~{int(sum(average_range_plus) / len(average_range_plus))} ] , the current value: {upperbondrange}"
    return text_range
    

def get_landmark_and_bbox(img_list,upperbondrange =0):
    frames = read_imgs(img_list)
    batch_size_fa = 1
    batches = [frames[i:i + batch_size_fa] for i in range(0, len(frames), batch_size_fa)]
    coords_list = []
    landmarks = []
    if upperbondrange != 0:
        print('get key_landmark and face bounding boxes with the bbox_shift:',upperbondrange)
    else:
        print('get key_landmark and face bounding boxes with the default value')
    average_range_minus = []
    average_range_plus = []
    for fb in tqdm(batches):
        face_land_mark = get_face_landmarks(np.asarray(fb)[0]).astype(np.int32)
        
        # get bounding boxes by face detetion
        bbox = fa.get_detections_for_batch(np.asarray(fb))
        
        # adjust the bounding box refer to landmark
        # Add the bounding box to a tuple and append it to the coordinates list
        for j, f in enumerate(bbox):
            if f is None: # no face in the image
                coords_list += [coord_placeholder]
                continue
            
            half_face_coord =  face_land_mark[29]#np.mean([face_land_mark[28], face_land_mark[29]], axis=0)
            range_minus = (face_land_mark[30]- face_land_mark[29])[1]
            range_plus = (face_land_mark[29]- face_land_mark[28])[1]
            average_range_minus.append(range_minus)
            average_range_plus.append(range_plus)
            if upperbondrange != 0:
                half_face_coord[1] = upperbondrange+half_face_coord[1] #手动调整  + 向下（偏29）  - 向上（偏28）
            half_face_dist = np.max(face_land_mark[:,1]) - half_face_coord[1]
            min_upper_bond = 0
            upper_bond = max(min_upper_bond, half_face_coord[1] - half_face_dist)
            
            f_landmark = (np.min(face_land_mark[:, 0]),int(upper_bond),np.max(face_land_mark[:, 0]),np.max(face_land_mark[:,1]))
            x1, y1, x2, y2 = f_landmark
            
            if y2-y1<=0 or x2-x1<=0 or x1<0: # if the landmark bbox is not suitable, reuse the bbox
                coords_list += [f]
                w,h = f[2]-f[0], f[3]-f[1]
                print("error bbox:",f)
            else:
                coords_list += [f_landmark]
    
    print("********************************************bbox_shift parameter adjustment**********************************************************")
    print(f"Total frame:「{len(frames)}」 Manually adjust range : [ -{int(sum(average_range_minus) / len(average_range_minus))}~{int(sum(average_range_plus) / len(average_range_plus))} ] , the current value: {upperbondrange}")
    print("*************************************************************************************************************************************")
    return coords_list,frames
    

if __name__ == "__main__":
    img_list = ["./results/lyria/00000.png","./results/lyria/00001.png","./results/lyria/00002.png","./results/lyria/00003.png"]
    crop_coord_path = "./coord_face.pkl"
    coords_list,full_frames = get_landmark_and_bbox(img_list)
    with open(crop_coord_path, 'wb') as f:
        pickle.dump(coords_list, f)
        
    for bbox, frame in zip(coords_list,full_frames):
        if bbox == coord_placeholder:
            continue
        x1, y1, x2, y2 = bbox
        crop_frame = frame[y1:y2, x1:x2]
        print('Cropped shape', crop_frame.shape)
        
        #cv2.imwrite(path.join(save_dir, '{}.png'.format(i)),full_frames[i][0][y1:y2, x1:x2])
    print(coords_list)
