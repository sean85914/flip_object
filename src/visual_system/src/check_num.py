import numpy as np
import cv2
import argparse

parser = argparse.ArgumentParser(description="Check nonzeros entries of difference of two images in given path")
parser.add_argument('--path', help="path to images", type=str)
parser.add_argument('--num', help='total amount of images', type=int)

args = parser.parse_args()

if __name__ == "__main__":
    arr = []
    for i in range(0, args.num+1, 2):
        img1_str = args.path + "{:0>6d}.png".format(i)
        img2_str = args.path + "{:0>6d}.png".format(i+1)
        print "Processing {}".format(img1_str)
        img1 = cv2.imread(img1_str, -1)
        img2 = cv2.imread(img2_str, -1)
        # Convert uint16 to int16
        img1_int = np.array(img1, dtype=np.int16)
        img2_int = np.array(img2, dtype=np.int16)
        subimg_1 = img1_int[21:245, 208:452]
        subimg_2 = img2_int[21:245, 208:452]
        result_img = subimg_1 - subimg_2
        non_zero_num = np.count_nonzero(result_img)
        print non_zero_num
        arr.append(non_zero_num)
    print "Mean: {}".format(int(np.mean(arr)))
    print "Max:  {}".format(np.max(arr))
    print "Min:  {}".format(np.min(arr))
