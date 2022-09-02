#!/usr/bin/env python
#-*- coding: utf-8 -*-

# Python 2/3 compatibility
from __future__ import print_function

# Built-in modules
import os
import math
import time
import multiprocessing

# External modules
import cv2
import numpy as np
from numpy.linalg import inv
from mpl_toolkits.mplot3d import Axes3D

# ROS module
import rospy
import message_filters
import tf2_ros
import ros_numpy
import image_geometry
from cv_bridge import CvBridge, CvBridgeError
from tf.transformations import euler_from_matrix
# from tf2_sensor_msgs.tf2_sensor_msgs import do_transform_cloud
from sensor_msgs.msg import PointCloud2, CompressedImage
import sensor_msgs.point_cloud2 as pc2
from sensor_msgs.msg import Image, CameraInfo, PointCloud2
from vision_msgs.msg import Detection2DArray, Detection2D
from geometry_msgs.msg import PoseArray, Pose

params_lidar = {
    "X": 0.0, # meter
    "Y": 0.0,
    "Z": 0.0,
    "YAW": 0.0, # deg
    "PITCH": 0.0,
    "ROLL": 0.0
}

params_cam = {
    "WIDTH": 640, # image width
    "HEIGHT": 480, # image height
    "FOV": 60, # Field of view
    "X": 0.145, # meter
    "Y": 0,
    "Z": -0.09,
    "YAW": 0.0, # deg
    "PITCH": 0.0,
    "ROLL": 0.0
}

# Global variables
# PAUSE = False
# FIRST_TIME = True
# KEY_LOCK = threading.Lock()
TF_BUFFER = None
TF_LISTENER = None
# CV_BRIDGE = CvBridge()
CAMERA_MODEL = image_geometry.PinholeCameraModel()

def getRotMat(RPY):
    cosR = math.cos(RPY[0])
    cosP = math.cos(RPY[1])
    cosY = math.cos(RPY[2])
    sinR = math.sin(RPY[0])
    sinP = math.sin(RPY[1])
    sinY = math.sin(RPY[2])
    
    rotRoll = np.array([1,0,0, 0,cosR,-sinR, 0,sinR,cosR]).reshape(3,3)
    rotPitch = np.array([cosP,0,sinP, 0,1,0, -sinP,0,cosP]).reshape(3,3)
    rotYaw = np.array([cosY,-sinY,0, sinY,cosY,0, 0,0,1]).reshape(3,3)
    
    rotMat = rotYaw.dot(rotPitch.dot(rotRoll))
    return rotMat

def getTransformMat(params_lidar, params_cam):
    #With Respect to Vehicle ISO Coordinate
    #camRPY = np.array([-90*math.pi/180,0,(-90+8.5)*math.pi/180])
    lidarPosition = np.array([params_lidar.get(i) for i in (["X","Y","Z"])])
    camPosition = np.array([params_cam.get(i) for i in (["X","Y","Z"])])

    lidarRPY = np.array([params_lidar.get(i) for i in (["ROLL","PITCH","YAW"])])
    camRPY = np.array([params_cam.get(i) for i in (["ROLL","PITCH","YAW"])])
    camRPY = camRPY + np.array([-90*math.pi/180,0,-90*math.pi/180])

    # lidarPositionOffset = np.array([0.0,0,0.02081])
    # lidarPosition = lidarPosition + lidarPositionOffset

    # camPositionOffset = np.array([0.1085, 0, 0])
    # camPosition = camPosition + camPositionOffset

    camRot = getRotMat(camRPY)
    camTransl = np.array([camPosition])
    Tr_cam_to_vehicle = np.concatenate((camRot,camTransl.T),axis = 1)
    Tr_cam_to_vehicle = np.insert(Tr_cam_to_vehicle, 3, values=[0,0,0,1],axis = 0)

    lidarRot = getRotMat(lidarRPY)
    lidarTransl = np.array([lidarPosition]) 
    Tr_lidar_to_vehicle = np.concatenate((lidarRot,lidarTransl.T),axis = 1)
    Tr_lidar_to_vehicle = np.insert(Tr_lidar_to_vehicle, 3, values=[0,0,0,1],axis = 0)

    invTr = inv(Tr_cam_to_vehicle)
    Tr_lidar_to_cam = invTr.dot(Tr_lidar_to_vehicle).round(6)
    # print(Tr_lidar_to_cam)
    return Tr_lidar_to_cam

# Should modify real intrinsic matrix
def getCameraMat(params_cam):
    # Camera Intrinsic Parameters
    focalLength = params_cam["WIDTH"]/(2*np.tan(np.deg2rad(params_cam["FOV"]/2)))
    principalX = params_cam["WIDTH"]/2
    principalY = params_cam["HEIGHT"]/2
    CameraMat = np.array([focalLength,0.,principalX,0,focalLength,principalY,0,0,1]).reshape(3,3)
    #print(CameraMat)
    return CameraMat

def transformLiDARToCamera(TransformMat, pc_lidar):
    cam_temp = TransformMat.dot(pc_lidar)
    cam_temp = np.delete(cam_temp, 3, axis=0)
    return cam_temp

def transformCameraToImage(width, height, CameraMat, pc_camera, pc_lidar):
    cam_temp = pc_camera
    lid_temp = pc_lidar
    img_temp = CameraMat.dot(pc_camera)
    cam_temp = np.delete(cam_temp,np.where(img_temp[2,:]<0),axis=1)
    lid_temp = np.delete(lid_temp,np.where(img_temp[2,:]<0),axis=1)
    img_temp = np.delete(img_temp,np.where(img_temp[2,:]<0),axis=1)
    img_temp /= img_temp[2,:]
    cam_temp = np.delete(cam_temp,np.where(img_temp[0,:]>width),axis=1)
    lid_temp = np.delete(lid_temp,np.where(img_temp[0,:]>width),axis=1)
    img_temp = np.delete(img_temp,np.where(img_temp[0,:]>width),axis=1)
    cam_temp = np.delete(cam_temp,np.where(img_temp[1,:]>height),axis=1)
    lid_temp = np.delete(lid_temp,np.where(img_temp[1,:]>height),axis=1)
    img_temp = np.delete(img_temp,np.where(img_temp[1,:]>height),axis=1)
    # cut outter points camera frame
    cam_temp = np.delete(cam_temp,np.where(img_temp[0,:]<0),axis=1)
    lid_temp = np.delete(lid_temp,np.where(img_temp[0,:]<0),axis=1)
    img_temp = np.delete(img_temp,np.where(img_temp[0,:]<0),axis=1)
    cam_temp = np.delete(cam_temp,np.where(img_temp[1,:]<0),axis=1)
    lid_temp = np.delete(lid_temp,np.where(img_temp[1,:]<0),axis=1)
    img_temp = np.delete(img_temp,np.where(img_temp[1,:]<0),axis=1)
    return img_temp, cam_temp, lid_temp

def draw_pts_img(img, xi, yi):
    point_np = img
    for ctr in zip(xi, yi):
        point_np = cv2.circle(point_np, ctr, 2, (0,255,0),-1)
    return point_np

# average method
def calc_distance_position1(points):
    mat_points = np.array(points).T
    position = []
    for coordinate in mat_points:
        avg = sum(coordinate)/len(coordinate)
        position.append(avg)
    tmp_position = np.array(position)
    dist = math.sqrt(tmp_position.dot(tmp_position))
    return dist, position

# mid point method
def calc_distance_position2(poinst):
    pass


# all topics are processed in this callback function
def callback(velodyne, yolo, image, image_pub=None):
    global CAMERA_MODEL, TF_BUFFER, TF_LISTENER

    # rospy.loginfo('Setting up camera model')
    # CAMERA_MODEL.fromCameraInfo(camera_info)
    rospy.loginfo('Fusion Processing')

    # TF listener
    TF_BUFFER = tf2_ros.Buffer()
    TF_LISTENER = tf2_ros.TransformListener(TF_BUFFER)

    width = params_cam["WIDTH"]
    height = params_cam["HEIGHT"]
    TransformMat = getTransformMat(params_cam, params_lidar)
    CameraMat = getCameraMat(params_cam)

    # image callback function
    np_arr = np.frombuffer(image.data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

    # lidar callback function
    point_list = []
    for point in pc2.read_points(velodyne, skip_nans=True):
        point_list.append((point[0], point[1], point[2], 1))
    pc_np = np.array(point_list, np.float32)

    # yolo callback function
    box_list = []
    for detection in yolo.detections:
        center_x = detection.bbox.center.x
        center_y = detection.bbox.center.y
        left_x = center_x - (detection.bbox.size_x / 2.0)
        left_y = center_y - (detection.bbox.size_y / 2.0)
        right_x = center_x + (detection.bbox.size_x / 2.0)
        right_y = center_y + (detection.bbox.size_y / 2.0)
        bbox_dict = {'center_point':[center_x, center_y],
                        'left_point':[left_x, left_y],
                        'right_point':[right_x, right_y]
        }
        box_list.append(bbox_dict)

    filtered_xyz_p = pc_np[:, 0:3]
    filtered_xyz_p = filtered_xyz_p.T
    xyz_p = pc_np[:, 0:3]
    xyz_p = np.insert(xyz_p,3,1,axis=1).T   # Transpose
    
    # filtering point cloud in front of camera
    filtered_xyz_p = np.delete(filtered_xyz_p,np.where(xyz_p[0,:]<0),axis=1)
    xyz_p = np.delete(xyz_p,np.where(xyz_p[0,:]<0),axis=1)
    filtered_xyz_p = np.delete(filtered_xyz_p,np.where(xyz_p[0,:]>10),axis=1)
    xyz_p = np.delete(xyz_p,np.where(xyz_p[0,:]>10),axis=1)
    filtered_xyz_p = np.delete(filtered_xyz_p,np.where(xyz_p[2,:]<-0.7),axis=1)
    xyz_p = np.delete(xyz_p,np.where(xyz_p[2,:]<-0.7),axis=1) #Ground Filter

    xyz_c = transformLiDARToCamera(TransformMat, xyz_p)
    xy_i, filtered_xyz_c, filtered_xyz_p = transformCameraToImage(width, height, CameraMat, xyz_c, filtered_xyz_p)

    mat_xyz_p = filtered_xyz_p.T
    mat_xyz_c = filtered_xyz_c.T
    mat_xy_i = xy_i.T

    # filtering points in bounding boxes & calculate position and distance
    dist_list = []
    position_list = []
    for i, box in enumerate(box_list):
        inner_3d_point = []
        for k, xy in enumerate(mat_xy_i):
            if xy[0] > box['left_point'][0] and xy[0] < box['right_point'][0] and xy[1] > box['left_point'][1] and xy[1] < box['right_point'][1]:
                inner_3d_point.append(mat_xyz_p[k].tolist())
        dist, position = calc_distance_position1(inner_3d_point)
        dist_list.append(dist)
        position_list.append(position)
    print('distance list: ', dist_list)
    print('position list: ', position_list)

    xy_i = xy_i.astype(np.int32)
    projectionImage = draw_pts_img(img, xy_i[0,:], xy_i[1,:])

    # cv2.imshow("LidartoCameraProjection", projectionImage)
    # cv2.waitKey(1)

# practical main function
def listener(image_color, velodyne_points, yolo_bbox):
    # Start node
    rospy.init_node('fusion_camera_lidar', anonymous=True)
    rospy.loginfo('Current PID: [%d]' % os.getpid())
    rospy.loginfo('PointCloud2 topic: %s' % velodyne_points)
    rospy.loginfo('YOLO topic: %s ' % yolo_bbox)
    rospy.loginfo('Image topic: %s' % image_color)

    # Subscribe to topics
    velodyne_sub = message_filters.Subscriber(velodyne_points, PointCloud2)
    yolo_sub = message_filters.Subscriber(yolo_bbox, Detection2DArray)
    image_sub = message_filters.Subscriber(image_color, Image)

    # Publish output topic
    obj_pub = rospy.Publisher('yolo_lidar', PoseArray, queue_size=5)

    # Synchronize the topic by time: velodyne, yolo, image
    ats = message_filters.ApproximateTimeSynchronizer(
        [velodyne_sub, yolo_sub, image_sub], queue_size=10, slop=0.1)
    ats.registerCallback(callback, obj_pub)

    # Keep python from exiting until this node is stopped
    try:
        rospy.spin()
    except rospy.ROSInterruptException:
        rospy.loginfo('Shutting down')

if __name__ == '__main__':
    # YOLO, LiDAR Topic name
    velodyne_points = '/velodyne_points'
    yolo_bbox = '/sign_bbox'
    image_color = '/usb_cam/image_raw'

    # Start subscriber
    listener(image_color, velodyne_points, yolo_bbox)
