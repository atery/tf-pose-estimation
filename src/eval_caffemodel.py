import pickle
import cv2, os
os.environ['GLOG_minloglevel'] = '2' 

import numpy as np
import time
import logging
import argparse
import json, re
from common import  read_imgfile, CocoColors
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
import caffe 
from estimator import PoseEstimator
transform_list = [0, 15,14,17,16,5, 2, 6, 3,7,4, 11, 8, 12, 9,13,10]
def write_coco_json(human, image_w, image_h):
    keypoints = []
    for i in transform_list:
        if i not in human.body_parts.keys():
            keypoints.extend([0,0,0])
            continue
        body_part = human.body_parts[i]
        center = (int(body_part.x * image_w + 0.5), int(body_part.y * image_h + 0.5))
        keypoints.append(center[0])
        keypoints.append(center[1])
        keypoints.append(2)
    return keypoints


def compute_oks(keypoints, anns):
    sigmas = np.array([.26, .25, .25, .35, .35, .79, .79, .72, .72, .62, .62, 1.07, 1.07, .87, .87, .89, .89]) / 10.0
    vars = (sigmas * 2) ** 2
    max_score = 0
    max_visible = []
    for ann in anns:
        score = 0
        gt = ann['keypoints']
        visible = gt[2::3]
        if np.sum(visible) == 0:
            continue
        else:
            gt_point = np.array([(x, y) for x , y in zip(gt[0::3], gt[1::3])])
            pred = np.array([(x, y) for x , y in zip(keypoints[0::3], keypoints[1::3])])
            dist = (gt_point - pred) ** 2
            dist = np.sum(dist, axis=1)
            sp = (ann['area'] + 1)

            for i in range(17):
                score = score if visible[i]== 0 else score +  np.exp(-dist[i]  / 2.0 / sp/ vars[i])
            max_score = max(score/np.count_nonzero(visible), max_score)
            max_visible = visible
    return  max_score, max_visible

def getLastName(file_name):
    if file_name.startswith('COCO_val2014_'):
        file_name = file_name.split('COCO_val2014_')[1]
    return float(re.sub("^0+", "", file_name).split('.')[0])

def compute_ap(score, threshold =0.5):
    b =  [ 1 if x > threshold else 0 for x in score]
    return np.sum(b)/1.0/len(score)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Tensorflow Openpose Inference')
    parser.add_argument('--imgpath', type=str, default='./images/p2.jpg')
    parser.add_argument('--input-width', type=int, default=368)
    parser.add_argument('--input-height', type=int, default=368)
    parser.add_argument('--model', type=str, default="cmupose")
    parser.add_argument('--image_dir', type=str, default='/home/zaikun/hdd/data/keypoint/val2017/')
    parser.add_argument('--coco_json_file', type = str, default = '/home/zaikun/hdd/data/keypoint/annotations/person_keypoints_val2017.json')
    parser.add_argument('--display', type=bool, default=False)
    parser.add_argument('--caffemodel', type=str, default='./models/pretrained/cmupose/pose_iter_440000.caffemodel')
    parser.add_argument('--proto', type=str, default='./models/pretrained/cmupose/pose_deploy_linevec.prototxt')

    args = parser.parse_args()
    write_json = 'json/%s_%d_%d.json' %(args.model, args.input_width, args.input_height)
    if not os.path.exists(write_json):
        fp = open(write_json, 'w')
        result = []
        val_images = os.listdir(args.image_dir)
        coco = COCO(args.coco_json_file)
        keys = list(coco.imgs.keys())

        caffe.set_mode_gpu()
        net = caffe.Net(args.proto, args.caffemodel, caffe.TEST)
        net.blobs['image'].reshape(*(1, 3, args.input_height, args.input_width))
        for i, img in enumerate(val_images):
            if i % 1000 == 0:
                print('running through {} examples'.format(i))
            image_id = int(getLastName(img))
            img_meta = coco.imgs[keys[i]]
            img_idx = img_meta['id']
            ann_idx = coco.getAnnIds(imgIds=image_id)

            item = {
                'image_id':1,
                'category_id':1,
                'keypoints':[],
                'score': 0.0
            }

            img_name = args.image_dir + img
            image = cv2.imread(img_name)
            image = cv2.resize(image, (args.input_width, args.input_height))

            net.blobs['image'].data[...] = np.transpose(np.float32(image[:,:,:,np.newaxis]), (3,2,0,1))/256 - 0.5;

            out = net.forward()
            concat_stage7 = out['net_output'][0]
            heatMat, pafMat = concat_stage7[:19, :, :], concat_stage7[19:, :, :]

            humans = PoseEstimator.estimate(heatMat, pafMat)
            if len(humans) == 0:
                continue

            for human in humans :
                r = write_coco_json(human, img_meta['width'], img_meta['height'])
                item['keypoints'] = r
                item['image_id'] = int(image_id)
                item['score'] , visible = compute_oks(r, coco.loadAnns(ann_idx))
                if len(visible) != 0:
                    for vis in range(17):
                        item['keypoints'][3* vis + 2] = visible[vis]
                    result.append(item)

        json.dump(result,fp)
        fp.close()
        annType = ['segm', 'bbox', 'keypoints']
        annType = annType[2]
        cocoGt = COCO(args.coco_json_file)
        imgIds = sorted(cocoGt.getImgIds())
        cocoDt = cocoGt.loadRes(write_json)
        cocoEval = COCOeval(cocoGt, cocoDt, annType)
        cocoEval.params.maxDets=[20]
        cocoEval.params.imgIds = imgIds
        cocoEval.params.catIds = [1]
        cocoEval.evaluate()
        cocoEval.accumulate()
        cocoEval.summarize()

    pred = json.load(open(write_json, 'r'))
    print('model {} with with {} and height {} AP50'.format(args.model, args.input_width, args.input_height))
    scores = [ x['score'] for x in pred]
    ap50 = compute_ap(scores, 0.5)
    print('ap50 is %f' % ap50)
    ap = 0
    for i in np.arange(0.5,1, 0.05).tolist():
        ap = ap + compute_ap(scores, i)
    ap = ap / len(np.arange(0.5, 1 , 0.05).tolist())
    print('ap is %f' % ap)

