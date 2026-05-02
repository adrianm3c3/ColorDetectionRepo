#!/usr/bin/env python3

import rospy
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

def move_arm():
    rospy.init_node("x3_arm_simple_move")

    pub = rospy.Publisher(
        "/arm_controller/command",
        JointTrajectory,
        queue_size=10
    )

    rospy.sleep(1.0)  # allow publisher to connect

    traj = JointTrajectory()
    traj.joint_names = [
        "joint1",
        "joint2",
        "joint3",
        "joint4",
        "joint5"
    ]

    point = JointTrajectoryPoint()

    # ===== TARGET POSITIONS (radians) =====
    point.positions = [
        0.0,     # base rotate
        -0.6,    # shoulder
        0.8,     # elbow
        0.0,     # wrist
        0.0      # gripper rotate
    ]

    point.time_from_start = rospy.Duration(2.0)

    traj.points.append(point)

    rospy.loginfo("Sending arm trajectory")
    pub.publish(traj)

if __name__ == "__main__":
    try:
        move_arm()
    except rospy.ROSInterruptException:
        pass
