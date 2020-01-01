import os
import sys
import cv2
import time
import numpy as np
import argparse
import torch
import rospy
import rospkg
import utils
import pickle
import serial
from trainer import Trainer
from collections import namedtuple, deque
from prioritized_memory import Memory
# srv
from std_srvs.srv import SetBool, SetBoolRequest, SetBoolResponse, \
                         Empty, EmptyRequest, EmptyResponse
from arm_operation.srv import agent_abb_action, agent_abb_actionRequest, agent_abb_actionResponse
from visual_system.srv import get_pc, get_pcRequest, get_pcResponse, \
                              pc_is_empty, pc_is_emptyRequest, pc_is_emptyResponse, \
                              check_grasp_success, check_grasp_successRequest, check_grasp_successResponse
from visualization.srv import viz_marker, viz_markerRequest, viz_markerResponse

# Define transition tuple
Transition = namedtuple('Transition', ['color', 'depth', 'pixel_idx', 'reward', 'next_color', 'next_depth', 'is_empty'])

# Parse argument
parser = argparse.ArgumentParser(prog="reinforcement_grasping", description="Reinforcement learning for robot arm grasping")
parser.add_argument("--is_testing", action="store_true", default=False, help="True if testing, default is false")
parser.add_argument("--episode", type=int, help="Which episode is this run?")
parser.add_argument("--force_cpu", action="store_true", default=False, help="True if using CPU, default is false")
parser.add_argument("--model", type=str, default="", help="If provided, continue training the model, or using this model for testing, default is empty srting")
parser.add_argument("--buffer_file", type=str, default="", help="If provided, will read the given file to construct the experience buffer, default is empty string")
parser.add_argument("--epsilon", type=float, default=0.5, help="Probability to choose random action")
parser.add_argument("--port", type=str, default="/dev/ttylight", help="Port for arduino, which controls the alram lamp, default is /dev/ttylight")
parser.add_argument("--buffer_size", type=int, default=500, help="Experience buffer size, default is 500") # N
parser.add_argument("--learning_freq", type=int, default=10, help="Frequency for updating behavior network, default is 10") # M
parser.add_argument("--updating_freq", type=int, default=40, help="Frequency for updating target network, default is 40") # C
parser.add_argument("--mini_batch_size", type=int, default=4, help="How many transitions should used for learning, default is 4") # K
parser.add_argument("--save_every", type=int, default=10, help="Every how many steps should save the model, default is 10")
parser.add_argument("--learning_rate", type=float, default=1e-4, help="Learning rate for the trainer, default is 1e-4")
args = parser.parse_args()
utils.show_args(args)

testing         = args.is_testing
episode         = args.episode
use_cpu         = args.force_cpu
model_str       = args.model
buffer_str      = args.buffer_file
epsilon         = args.epsilon if not testing else 1
port            = args.port
buffer_size     = args.buffer_size
learning_freq   = args.learning_freq
updating_freq   = args.updating_freq
mini_batch_size = args.mini_batch_size
save_every      = args.save_every
learning_rate   = args.learning_rate
# Constant
reward = 1.0
discount_factor = 0.5
iteration = 0
t = 0
return_ = 0.0
z_thres = -0.017
program_ts = time.time()
memory = Memory(buffer_size)
arduino = serial.Serial(port, 115200)

if model_str == "" and testing: # TEST SHOULD PROVIDE MODEL
	print "\033[0;31mNo model provided, exit!\033[0m"
	os._exit(0)
	
if buffer_str != "":
	memory.load_memory(buffer_file)

# trainer
trainer = Trainer(reward, discount_factor, use_cpu, learning_rate)
# Still using small learning rate to backpropagate when testing
if testing:
	for param_group in trainer.optimizer.param_groups:
		param_group['lr'] = 1e-5

# Load model if provided
if model_str != "":
	print "[%f]: Loading provided model..." %(time.time())
	trainer.behavior_net.load_state_dict(torch.load(model_str))
	trainer.target_net.load_state_dict(trainer.behavior_net.state_dict())
	
# Get logger path
r = rospkg.RosPack()
package_path = r.get_path("grasp_suck")
csv_path, image_path, depth_path, mixed_paths, feat_paths, pc_path, model_path, vis_path, diff_path = utils.getLoggerPath(testing, package_path, episode)
# Service clients
vacuum_pump_control      = rospy.ServiceProxy("/vacuum_pump_control_node/vacuum_control", SetBool)
check_suck_success       = rospy.ServiceProxy("/vacuum_pump_control_node/check_suck_success", SetBool)
agent_take_action_client = rospy.ServiceProxy("/agent_server_node/agent_take_action", agent_abb_action)
get_pc_client            = rospy.ServiceProxy("/combine_pc_node/get_pc", get_pc)
empty_checker            = rospy.ServiceProxy("/combine_pc_node/empty_state", pc_is_empty)
check_grasp_success      = rospy.ServiceProxy("/combine_pc_node/grasp_state", check_grasp_success)
go_home                  = rospy.ServiceProxy("/agent_server_node/go_home", Empty)
go_place                 = rospy.ServiceProxy("/agent_server_node/go_place", Empty)
viz                      = rospy.ServiceProxy("/viz_marker_node/viz_marker", viz_marker)
# Result list
action_list   = [] # If action valid?
target_list   = [] # Takes action at which `pixel`
position_list = [] # Takes at which `3D position`
result_list   = [] # If action success?
loss_list     = [] # Training loss
explore_list  = [] # If using explore?
return_list   = [] # Episode return
episode_list  = [] # Episode step length

# Initialize
go_home()
vacuum_pump_control(SetBoolRequest(False))

try:
	while True:
		if iteration is not 0: arduino.write("gb 1000") # Green + buzzer for alarming resetting
		cmd = raw_input("\033[1;34m[%f] Reset environment, if ready, press 's' to start. 'e' to exit: \033[0m" %(time.time()-program_ts))
		if cmd == 'E' or cmd == 'e':
			utils.saveFiles(action_list, target_list, result_list, loss_list, explore_list, return_list, episode_list, position_list, csv_path)
			# Save experience buffer
			memory.save_memory(csv_path)
			print "Regular shutdown"
			sys.exit(0)
		elif cmd == 'S' or cmd == 's':
			episode_list.append(t)
			t = 0
			if iteration is not 0: return_list.append(return_)
			return_ = 0.0
			is_empty = False
			while is_empty is not True:
				print "\033[0;32m[%f] Iteration: %d\033[0m" %(time.time()-program_ts, iteration)
				if not testing: epsilon_ = max(epsilon * np.power(0.998, t), 0.1) # half after 350 steps
				get_pc_req = get_pcRequest()
				get_pc_req.file_name = pc_path + "{:06}_before.pcd".format(iteration)
				pc_response = get_pc_client(get_pc_req)
				color, depth, points = utils.get_heightmap(pc_response.pc, image_path, depth_path, iteration)
				ts = time.time()
				suck_1_prediction, suck_2_prediction, grasp_prediction = trainer.forward(color, depth, is_volatile=True)
				print "Forward past: {} seconds".format(time.time()-ts)
				# Save heatmap and combine with color heightmap
				heatmaps   = []
				mixed_imgs = []
				heatmaps.append(utils.vis_affordance(suck_1_prediction[0]))
				heatmaps.append(utils.vis_affordance(suck_2_prediction[0]))
				for grasp_angle_prediction in grasp_prediction:
					heatmaps.append(utils.vis_affordance(grasp_angle_prediction))
				for heatmap_idx in range(len(heatmaps)):
					img_name = feat_paths[heatmap_idx] + "{:06}.jpg".format(iteration)
					cv2.imwrite(img_name, heatmaps[heatmap_idx])
					img_name = mixed_paths[heatmap_idx] + "{:06}.jpg".format(iteration)
					mixed = cv2.addWeighted(color, 1.0, heatmaps[heatmap_idx], 0.4, 0)
					mixed_imgs.append(mixed)
					cv2.imwrite(img_name, mixed)
				# Standarize predictions to avoid bias between them
				suck_1_prediction = utils.standarization(suck_1_prediction)
				suck_2_prediction = utils.standarization(suck_2_prediction)
				for i in range(len(grasp_prediction)):
					grasp_prediction[i] = utils.standarization(grasp_prediction[i])
				explore = False
				if not testing: # Train
					explore, action, action_str, pixel_index, angle = utils.epsilon_greedy_policy(epsilon_, suck_1_prediction, suck_2_prediction, grasp_prediction, depth, diff_path, iteration)
				else: # Testing
					action, action_str, pixel_index, angle = utils.greedy_policy(suck_1_prediction, suck_2_prediction, grasp_prediction)
				explore_list.append(explore)
				target_list.append(pixel_index)
				position_list.append(points[pixel_index[1], pixel_index[2]])
				del suck_1_prediction, suck_2_prediction, grasp_prediction
				print "Take action [\033[1;31m%s\033[0m] at (%d, %d) -> (%f, %f, %f)" %(action_str, pixel_index[1], pixel_index[2], \
				                                                                        points[pixel_index[1], pixel_index[2], 0],
				                                                                        points[pixel_index[1], pixel_index[2], 1],
				                                                                        points[pixel_index[1], pixel_index[2], 2])
				# Save color heightmap + prediction heatmap + motion primitive and corresponding position, then show it
				visual_img = utils.draw_image(mixed_imgs[pixel_index[0]], explore, pixel_index)
				img_name = vis_path + "vis_{:06}.jpg".format(iteration)
				cv2.imwrite(img_name, visual_img)
				cv2.imshow("prediction", visual_img)
				cv2.waitKey(33)
				# Check if action valid (is NAN?)
				is_valid = utils.check_if_valid(points[pixel_index[1], pixel_index[2]])
				# Visualize in RViz
				viz_req = viz_markerRequest()
				viz_req.point.x = points[pixel_index[1], pixel_index[2], 0]
				viz_req.point.y = points[pixel_index[1], pixel_index[2], 1]
				viz_req.point.z = points[pixel_index[1], pixel_index[2], 2]
				viz_req.primitive = action
				viz_req.angle = angle
				viz_req.valid = is_valid
				viz(viz_req)
				if is_valid: # Only take action if valid
					action_list.append(action)
					agent_action = agent_abb_actionRequest()
					if pixel_index[0] == 0: # suck_1 -> 3
						agent_action.tool_id = 3
					elif pixel_index[0] == 1: # suck_2 -> 2
						agent_action.tool_id = 2
					else:
						agent_action.tool_id = 1 # grasp
					agent_action.position = viz_req.point
					agent_action.angle = angle
					agent_response = agent_take_action_client(agent_action)
					go_home()
				else: # invalid
					action_list.append(-1); arduino.write("r 1000") # Red
					action_success = False
				if is_valid:
					if action < 2: # suction cup
						action_success = check_suck_success().success
					else: # parallel-jaw gripper TODO
						check_grasp_success_request = check_grasp_successRequest()
						check_grasp_success_request.prior_pcd_str = pc_path + "{:06}_before.pcd".format(iteration)
						# Get temporary pc and save file
						get_pc_req.file_name = pc_path + "{:06}_after.pcd".format(iteration)
						tmp_response = get_pc_client(get_pc_req)
						check_grasp_success_request.post_pcd_str = pc_path + "{:06}_after.pcd".format(iteration)
						check_grasp_success_request.operated_position = viz_req.point
						action_success = check_grasp_success(check_grasp_success_request).is_success
				result_list.append(action_success)
				if action_success: go_place(); arduino.write("g 1000") # Green
				else: vacuum_pump_control(SetBoolRequest(False)); arduino.write("o 1000") # Orange
				time.sleep(0.5)
				# Get next images, and check if bin is empty
				get_pc_req.file_name = pc_path + "{:06}_after.pcd".format(iteration)
				next_pc = get_pc_client(get_pc_req).pc
				next_color, next_depth, next_points = utils.get_heightmap(next_pc, image_path + "next_", depth_path + "next_", iteration)
				empty_checker_req = pc_is_emptyRequest()
				empty_checker_req.input_pc = next_pc
				is_empty = empty_checker(empty_checker_req).is_empty.data
				# Reward shaping
				# Success -> +R
				# Fail and z>=z_thres -> -R
				# Fail and z< z_thres -> -2R
				# Invalid -> -5R
				if not is_valid: # NaN point
					current_reward = -5*reward
				elif action_success: # Good
					current_reward = reward
				elif is_valid and not action_success and points[pixel_index[1], pixel_index[2], 2] >= z_thres: # Bad suction point
					current_reward = -reward
				elif not action_success and points[pixel_index[1], pixel_index[2], 2] < z_thres: # Suck on box
					current_reward = -2*reward
				return_ += current_reward * np.power(discount_factor, t) 
				print "\033[1;33mCurrent reward: {} \t Return: {}\033[0m".format(current_reward, return_)
				# Store transition to experience buffer
				color_name = image_path + "color_{:06}.jpg".format(iteration)
				depth_name = depth_path + "depth_data_{:06}.txt".format(iteration)
				next_color_name = image_path + "next_color_{:06}.jpg".format(iteration)
				next_depth_name = depth_path + "next_depth_data_{:06}.txt".format(iteration)
				transition = Transition(color_name, depth_name, pixel_index, current_reward, next_color_name, next_depth_name, is_empty)
				# Compute TD error
				td_target = trainer.get_label_value(current_reward, next_color, next_depth, is_empty)
				rotation_idx = pixel_index[0]-2 if "grasp" in action_str else -1
				old_value = trainer.forward(color, depth, action_str, False, rotation_idx)[0, pixel_index[1], pixel_index[2]]
				memory.add((td_target-old_value)**2, transition)
				iteration += 1
				t += 1
				if iteration % learning_freq == 0: # Update parameters
					arduino.write("b 1000")
					mini_batch, idxs, is_weight = memory.sample(mini_batch_size)
					sampled_iter = []
					for i in range(mini_batch_size):
						iter_str = mini_batch[i].color[mini_batch[i].color.find("color_")+6:-4]
						sampled_iter.append(int(iter_str))
						color = cv2.imread(mini_batch[i].color)
						depth = np.loadtxt(mini_batch[i].depth)
						pixel_index = mini_batch[i].pixel_idx
						next_color = cv2.imread(mini_batch[i].next_color)
						next_depth = np.loadtxt(mini_batch[i].next_depth)
						td_target = trainer.get_label_value(mini_batch[i].reward, next_color, next_depth, mini_batch[i].is_empty)
						trainer.backprop(color, depth, pixel_index, td_target, is_weight)
					print "Sampled at iteration: ", sampled_iter
					# After parameter updated, update prioirites tree
					for i in range(mini_batch_size):
						color = cv2.imread(mini_batch[i].color)
						depth = np.loadtxt(mini_batch[i].depth)
						pixel_index = mini_batch[i].pixel_idx
						next_color = cv2.imread(mini_batch[i].next_color)
						next_depth = np.loadtxt(mini_batch[i].next_depth)
						td_target = trainer.get_label_value(mini_batch[i].reward, next_color, next_depth, mini_batch[i].is_empty)
						if pixel_index[0] == 0:
							action_str = "suck_1"; rotate_idx = -1
						elif pixel_index[0] == 1:
							action_str = "suck_2"; rotate_idx = -1
						else:
							action_str = "grasp"; rotate_idx = pixel_index[0] -2
						old_value = trainer.forward(color, depth, action_str, False, rotate_idx)[0, pixel_index[1], pixel_index[2]]
						memory.update(idxs[i], (td_target-old_value)**2)
					arduino.write("b 1000"); print "\n[%f] Complete" %(time.time()-program_ts)
				if iteration % updating_freq == 0:
					print "[%f] Replace target network to behavior network" %(time.time()-program_ts)
					trainer.target_net.load_state_dict(trainer.behavior_net.state_dict)
				if iteration % save_every == 0 and not testing:
					model_name = model_path + "{}.pth".format(iteration)
					torch.save(trainer.behavior_net.state_dict(), model_name)
					print "[%f] Model: %s saved" %(time.time()-program_ts, model_name)
					
except KeyboardInterrupt:
	utils.saveFiles(action_list, target_list, result_list, loss_list, explore_list, return_list, episode_list, position_list, csv_path)
	# Save experience buffer
	memory.save_memory(csv_path)
	model_name = model_path + "interrupt_{}.pth".format(iteration)
	torch.save(trainer.behavior_net.state_dict(), model_name)
	utils.saveFiles(action_list, target_list, result_list, loss_list, explore_list, return_list, episode_list, position_list, csv_path)
	print "[%f] Model: %s saved" %(time.time()-program_ts, model_name)
	print "Shutdown since keyboard interrupt"
	sys.exit(0)
