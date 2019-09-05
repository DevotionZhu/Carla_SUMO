#!/usr/bin/env python

# Copyright (c) 2018 Intel Labs.
# authors: German Ros (german.ros@intel.com)
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""
    Example of automatic vehicle control from client side.
"""

from __future__ import print_function

import argparse
import collections
import datetime
import glob
import logging
import math
import os
import random
import re
import sys
import weakref
import time

try:
    import pygame
    from pygame.locals import KMOD_CTRL
    from pygame.locals import KMOD_SHIFT
    from pygame.locals import K_0
    from pygame.locals import K_9
    from pygame.locals import K_BACKQUOTE
    from pygame.locals import K_BACKSPACE
    from pygame.locals import K_COMMA
    from pygame.locals import K_DOWN
    from pygame.locals import K_ESCAPE
    from pygame.locals import K_F1
    from pygame.locals import K_LEFT
    from pygame.locals import K_PERIOD
    from pygame.locals import K_RIGHT
    from pygame.locals import K_SLASH
    from pygame.locals import K_SPACE
    from pygame.locals import K_TAB
    from pygame.locals import K_UP
    from pygame.locals import K_a
    from pygame.locals import K_c
    from pygame.locals import K_d
    from pygame.locals import K_h
    from pygame.locals import K_m
    from pygame.locals import K_p
    from pygame.locals import K_q
    from pygame.locals import K_r
    from pygame.locals import K_s
    from pygame.locals import K_w
except ImportError:
    raise RuntimeError('cannot import pygame, make sure pygame package is installed')

try:
    import numpy as np
except ImportError:
    raise RuntimeError(
        'cannot import numpy, make sure numpy package is installed')

# ==============================================================================
# -- find carla module ---------------------------------------------------------
# ==============================================================================
try:
    sys.path.append('/home/autolab/0.9.4/PythonAPI/carla-0.9.4-py3.5-linux-x86_64.egg')

    # sys.path.append('/home/autolab/zwh/SUMO_Carla/CarlaClient/PythonAPI/carla-0.9.6-py3.5-linux-x86_64.egg')
    sys.path.append(glob.glob('**/carla-*%d.%d-%s.egg' % (
        sys.version_info.major,
        sys.version_info.minor,
        'win-amd64' if os.name == 'nt' else 'linux-x86_64'))[0])
except IndexError:
    pass

import carla
from carla import ColorConverter as cc
from agents.navigation.roaming_agent import *
from agents.navigation.basic_agent import *



# ==============================================================================
# -- Global functions ----------------------------------------------------------
# ==============================================================================

def find_weather_presets():
    rgx = re.compile('.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)')
    name = lambda x: ' '.join(m.group(0) for m in rgx.finditer(x))
    presets = [x for x in dir(carla.WeatherParameters) if re.match('[A-Z].+', x)]
    return [(getattr(carla.WeatherParameters, x), name(x)) for x in presets]


def get_actor_display_name(actor, truncate=250):
    name = ' '.join(actor.type_id.replace('_', '.').title().split('.')[1:])
    return (name[:truncate-1] + u'\u2026') if len(name) > truncate else name


# ==============================================================================
# -- World ---------------------------------------------------------------
# ==============================================================================

class World(object):
    def __init__(self, carla_world, hud):
        self.world = carla_world
        self.map = self.world.get_map()
        self.hud = hud
        self.vehicle = None
        self.collision_sensor = None
        self.lane_invasion_sensor = None
        self.camera_manager = None
        self._weather_presets = find_weather_presets()
        self._weather_index = 0
        self.restart()
        self.world.on_tick(hud.on_world_tick)

    def restart(self):
        # Keep same camera config if the camera manager exists.
        cam_index = self.camera_manager._index if self.camera_manager is not None else 0
        cam_pos_index = self.camera_manager._transform_index if self.camera_manager is not None else 0

        blueprint = self.world.get_blueprint_library().find('vehicle.lincoln.mkz2017')
        blueprint.set_attribute('role_name', 'hero')
        if blueprint.has_attribute('color'):
            color = random.choice(blueprint.get_attribute('color').recommended_values)
            blueprint.set_attribute('color', color)

        # Spawn the vehicle.
        if self.vehicle is not None:
            spawn_point = self.vehicle.get_transform()
            spawn_point.location.z += 2.0
            spawn_point.rotation.roll = 0.0
            spawn_point.rotation.pitch = 0.0
            self.destroy()

            spawn_points = self.map.get_spawn_points()
            spawn_point = spawn_points[1]
            self.vehicle = self.world.spawn_actor(blueprint, spawn_point)

        while self.vehicle is None:
            spawn_points = self.map.get_spawn_points()
            spawn_point = spawn_points[1]
            self.vehicle = self.world.spawn_actor(blueprint, spawn_point)

        # Set up the sensors.
        self.collision_sensor = CollisionSensor(self.vehicle, self.hud)
        self.lane_invasion_sensor = LaneInvasionSensor(self.vehicle, self.hud)
        self.camera_manager = CameraManager(self.vehicle, self.hud)
        self.camera_manager._transform_index = cam_pos_index
        self.camera_manager.set_sensor(cam_index, notify=False)
        actor_type = get_actor_display_name(self.vehicle)
        self.hud.notification(actor_type)

    def next_weather(self, reverse=False):
        self._weather_index += -1 if reverse else 1
        self._weather_index %= len(self._weather_presets)
        preset = self._weather_presets[self._weather_index]
        self.hud.notification('Weather: %s' % preset[1])
        self.vehicle.get_world().set_weather(preset[0])

    def tick(self, clock):
        self.hud.tick(self, clock)

    def render(self, display):
        self.camera_manager.render(display)
        self.hud.render(display)

    def destroy(self):
        actors = [
            self.camera_manager.sensor,
            self.collision_sensor.sensor,
            self.lane_invasion_sensor.sensor,
            self.vehicle]
        for actor in actors:
            if actor is not None:
                actor.destroy()


# ==============================================================================
# -- KeyboardControl -----------------------------------------------------------
# ==============================================================================

class KeyboardControl(object):
    def __init__(self, world, start_in_autopilot):
        self._autopilot_enabled = start_in_autopilot
        self._control = carla.VehicleControl()
        self._steer_cache = 0.0
        world.vehicle.set_autopilot(self._autopilot_enabled)
        world.hud.notification("Press 'H' or '?' for help.", seconds=4.0)

    def parse_events(self, world, clock):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return True
            elif event.type == pygame.KEYUP:
                if self._is_quit_shortcut(event.key):
                    return True
                elif event.key == K_BACKSPACE:
                    world.restart()
                elif event.key == K_F1:
                    world.hud.toggle_info()
                elif event.key == K_h or (event.key == K_SLASH and pygame.key.get_mods() & KMOD_SHIFT):
                    world.hud.help.toggle()
                elif event.key == K_TAB:
                    world.camera_manager.toggle_camera()
                elif event.key == K_c and pygame.key.get_mods() & KMOD_SHIFT:
                    world.next_weather(reverse=True)
                elif event.key == K_c:
                    world.next_weather()
                elif event.key == K_BACKQUOTE:
                    world.camera_manager.next_sensor()
                elif event.key > K_0 and event.key <= K_9:
                    world.camera_manager.set_sensor(event.key - 1 - K_0)
                elif event.key == K_r:
                    world.camera_manager.toggle_recording()
                elif event.key == K_q:
                    self._control.gear = 1 if self._control.reverse else -1
                elif event.key == K_m:
                    self._control.manual_gear_shift = not self._control.manual_gear_shift
                    self._control.gear = world.vehicle.get_control().gear
                    world.hud.notification(
                        '%s Transmission' % ('Manual' if self._control.manual_gear_shift else 'Automatic'))
                elif self._control.manual_gear_shift and event.key == K_COMMA:
                    self._control.gear = max(-1, self._control.gear - 1)
                elif self._control.manual_gear_shift and event.key == K_PERIOD:
                    self._control.gear = self._control.gear + 1
                elif event.key == K_p:
                    self._autopilot_enabled = not self._autopilot_enabled
                    world.vehicle.set_autopilot(self._autopilot_enabled)
                    world.hud.notification('Autopilot %s' % ('On' if self._autopilot_enabled else 'Off'))
        if not self._autopilot_enabled:
            self._parse_keys(pygame.key.get_pressed(), clock.get_time())
            self._control.reverse = self._control.gear < 0

    def _parse_keys(self, keys, milliseconds):
        self._control.throttle = 1.0 if keys[K_UP] or keys[K_w] else 0.0
        steer_increment = 5e-4 * milliseconds
        if keys[K_LEFT] or keys[K_a]:
            self._steer_cache -= steer_increment
        elif keys[K_RIGHT] or keys[K_d]:
            self._steer_cache += steer_increment
        else:
            self._steer_cache = 0.0
        self._steer_cache = min(0.7, max(-0.7, self._steer_cache))
        self._control.steer = round(self._steer_cache, 1)
        self._control.brake = 1.0 if keys[K_DOWN] or keys[K_s] else 0.0
        self._control.hand_brake = keys[K_SPACE]

    @staticmethod
    def _is_quit_shortcut(key):
        return (key == K_ESCAPE) or (key == K_q and pygame.key.get_mods() & KMOD_CTRL)


# ==============================================================================
# -- HUD -----------------------------------------------------------------
# ==============================================================================


class HUD(object):
    def __init__(self, width, height):
        self.dim = (width, height)
        font = pygame.font.Font(pygame.font.get_default_font(), 20)
        fonts = [x for x in pygame.font.get_fonts() if 'mono' in x]
        default_font = 'ubuntumono'
        mono = default_font if default_font in fonts else fonts[0]
        mono = pygame.font.match_font(mono)
        self._font_mono = pygame.font.Font(mono, 14)
        self._notifications = FadingText(font, (width, 40), (0, height - 40))
        self.help = HelpText(pygame.font.Font(mono, 24), width, height)
        self.server_fps = 0
        self.frame_number = 0
        self.simulation_time = 0
        self._show_info = True
        self._info_text = []
        self._server_clock = pygame.time.Clock()

    def on_world_tick(self, timestamp):
        self._server_clock.tick()
        self.server_fps = self._server_clock.get_fps()
        self.frame_number = timestamp.frame_count
        self.simulation_time = timestamp.elapsed_seconds

    def tick(self, world, clock):
        if not self._show_info:
            return
        t = world.vehicle.get_transform()
        v = world.vehicle.get_velocity()
        c = world.vehicle.get_control()
        heading = 'N' if abs(t.rotation.yaw) < 89.5 else ''
        heading += 'S' if abs(t.rotation.yaw) > 90.5 else ''
        heading += 'E' if 179.5 > t.rotation.yaw > 0.5 else ''
        heading += 'W' if -0.5 > t.rotation.yaw > -179.5 else ''
        colhist = world.collision_sensor.get_collision_history()
        collision = [colhist[x + self.frame_number - 200] for x in range(0, 200)]
        max_col = max(1.0, max(collision))
        collision = [x / max_col for x in collision]
        vehicles = world.world.get_actors().filter('vehicle.*')
        self._info_text = [
            'Server:  % 16d FPS' % self.server_fps,
            '',
            'Vehicle: % 20s' % get_actor_display_name(world.vehicle, truncate=20),
            'Map:     % 20s' % world.map.name,
            'Simulation time: % 12s' % datetime.timedelta(seconds=int(self.simulation_time)),
            '',
            'Speed:   % 15.0f km/h' % (3.6 * math.sqrt(v.x**2 + v.y**2 + v.z**2)),
            u'Heading:% 16.0f\N{DEGREE SIGN} % 2s' % (t.rotation.yaw, heading),
            'Location:% 20s' % ('(% 5.1f, % 5.1f)' % (t.location.x, t.location.y)),
            'Height:  % 18.0f m' % t.location.z,
            '',
            ('Throttle:', c.throttle, 0.0, 1.0),
            ('Steer:', c.steer, -1.0, 1.0),
            ('Brake:', c.brake, 0.0, 1.0),
            ('Reverse:', c.reverse),
            ('Hand brake:', c.hand_brake),
            ('Manual:', c.manual_gear_shift),
            'Gear:        %s' % {-1: 'R', 0: 'N'}.get(c.gear, c.gear),
            '',
            'Collision:',
            collision,
            '',
            'Number of vehicles: % 8d' % len(vehicles)
        ]
        if len(vehicles) > 1:
            self._info_text += ['Nearby vehicles:']
            distance = lambda l: math.sqrt((l.x - t.location.x)**2 + (l.y - t.location.y)**2 + (l.z - t.location.z)**2)
            vehicles = [(distance(x.get_location()), x) for x in vehicles if x.id != world.vehicle.id]
            for d, vehicle in sorted(vehicles):
                if d > 200.0:
                    break
                vehicle_type = get_actor_display_name(vehicle, truncate=22)
                self._info_text.append('% 4dm %s' % (d, vehicle_type))
        self._notifications.tick(world, clock)

    def toggle_info(self):
        self._show_info = not self._show_info

    def notification(self, text, seconds=2.0):
        self._notifications.set_text(text, seconds=seconds)

    def error(self, text):
        self._notifications.set_text('Error: %s' % text, (255, 0, 0))

    def render(self, display):
        if self._show_info:
            info_surface = pygame.Surface((220, self.dim[1]))
            info_surface.set_alpha(100)
            display.blit(info_surface, (0, 0))
            v_offset = 4
            bar_h_offset = 100
            bar_width = 106
            for item in self._info_text:
                if v_offset + 18 > self.dim[1]:
                    break
                if isinstance(item, list):
                    if len(item) > 1:
                        points = [(x + 8, v_offset + 8 + (1.0 - y) * 30) for x, y in enumerate(item)]
                        pygame.draw.lines(display, (255, 136, 0), False, points, 2)
                    item = None
                    v_offset += 18
                elif isinstance(item, tuple):
                    if isinstance(item[1], bool):
                        rect = pygame.Rect((bar_h_offset, v_offset + 8), (6, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect, 0 if item[1] else 1)
                    else:
                        rect_border = pygame.Rect((bar_h_offset, v_offset + 8), (bar_width, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect_border, 1)
                        f = (item[1] - item[2]) / (item[3] - item[2])
                        if item[2] < 0.0:
                            rect = pygame.Rect((bar_h_offset + f * (bar_width - 6), v_offset + 8), (6, 6))
                        else:
                            rect = pygame.Rect((bar_h_offset, v_offset + 8), (f * bar_width, 6))
                        pygame.draw.rect(display, (255, 255, 255), rect)
                    item = item[0]
                if item: # At this point has to be a str.
                    surface = self._font_mono.render(item, True, (255, 255, 255))
                    display.blit(surface, (8, v_offset))
                v_offset += 18
        self._notifications.render(display)
        self.help.render(display)


# ==============================================================================
# -- FadingText ----------------------------------------------------------------
# ==============================================================================

class FadingText(object):
    def __init__(self, font, dim, pos):
        self.font = font
        self.dim = dim
        self.pos = pos
        self.seconds_left = 0
        self.surface = pygame.Surface(self.dim)

    def set_text(self, text, color=(255, 255, 255), seconds=2.0):
        text_texture = self.font.render(text, True, color)
        self.surface = pygame.Surface(self.dim)
        self.seconds_left = seconds
        self.surface.fill((0, 0, 0, 0))
        self.surface.blit(text_texture, (10, 11))

    def tick(self, _, clock):
        delta_seconds = 1e-3 * clock.get_time()
        self.seconds_left = max(0.0, self.seconds_left - delta_seconds)
        self.surface.set_alpha(500.0 * self.seconds_left)

    def render(self, display):
        display.blit(self.surface, self.pos)

# ==============================================================================
# -- HelpText ------------------------------------------------------------------
# ==============================================================================


class HelpText(object):
    def __init__(self, font, width, height):
        lines = __doc__.split('\n')
        self.font = font
        self.dim = (680, len(lines) * 22 + 12)
        self.pos = (0.5 * width - 0.5 * self.dim[0], 0.5 * height - 0.5 * self.dim[1])
        self.seconds_left = 0
        self.surface = pygame.Surface(self.dim)
        self.surface.fill((0, 0, 0, 0))
        for n, line in enumerate(lines):
            text_texture = self.font.render(line, True, (255, 255, 255))
            self.surface.blit(text_texture, (22, n * 22))
            self._render = False
        self.surface.set_alpha(220)

    def toggle(self):
        self._render = not self._render

    def render(self, display):
        if self._render:
            display.blit(self.surface, self.pos)

# ==============================================================================
# -- CollisionSensor -----------------------------------------------------------
# ==============================================================================


class CollisionSensor(object):
    def __init__(self, parent_actor, hud):
        self.sensor = None
        self._history = []
        self._parent = parent_actor
        self._hud = hud
        world = self._parent.get_world()
        bp = world.get_blueprint_library().find('sensor.other.collision')
        self.sensor = world.spawn_actor(bp, carla.Transform(), attach_to=self._parent)
        # We need to pass the lambda a weak reference to self to avoid circular
        # reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(lambda event: CollisionSensor._on_collision(weak_self, event))

    def get_collision_history(self):
        history = collections.defaultdict(int)
        for frame, intensity in self._history:
            history[frame] += intensity
        return history

    @staticmethod
    def _on_collision(weak_self, event):
        self = weak_self()
        if not self:
            return
        actor_type = get_actor_display_name(event.other_actor)
        self._hud.notification('Collision with %r, id = %d' % (actor_type, event.other_actor.id))
        impulse = event.normal_impulse
        intensity = math.sqrt(impulse.x ** 2 + impulse.y ** 2 + impulse.z ** 2)
        self._history.append((event.frame_number, intensity))
        if len(self._history) > 4000:
            self._history.pop(0)


# ==============================================================================
# -- LaneInvasionSensor --------------------------------------------------------
# ==============================================================================

class LaneInvasionSensor(object):
    def __init__(self, parent_actor, hud):
        self.sensor = None
        self._parent = parent_actor
        self._hud = hud
        world = self._parent.get_world()
        bp = world.get_blueprint_library().find('sensor.other.lane_detector')
        self.sensor = world.spawn_actor(bp, carla.Transform(), attach_to=self._parent)
        # We need to pass the lambda a weak reference to self to avoid circular
        # reference.
        weak_self = weakref.ref(self)
        self.sensor.listen(lambda event: LaneInvasionSensor._on_invasion(weak_self, event))

    @staticmethod
    def _on_invasion(weak_self, event):
        self = weak_self()
        if not self:
            return
        text = ['%r' % str(x).split()[-1] for x in set(event.crossed_lane_markings)]
        self._hud.notification('Crossed line %s' % ' and '.join(text))


# ==============================================================================
# -- CameraManager -------------------------------------------------------------
# ==============================================================================

class CameraManager(object):
    def __init__(self, parent_actor, hud):
        self.sensor = None
        self._surface = None
        self._parent = parent_actor
        self._hud = hud
        self._recording = False
        self._camera_transforms = [
            carla.Transform(carla.Location(x=-5.5, z=2.8), carla.Rotation(pitch=-15)),
            carla.Transform(carla.Location(x=1.6, z=1.7))]
        self._transform_index = 1
        self._sensors = [
            ['sensor.camera.rgb', cc.Raw, 'Camera RGB'],
            ['sensor.camera.depth', cc.Raw, 'Camera Depth (Raw)'],
            ['sensor.camera.depth', cc.Depth, 'Camera Depth (Gray Scale)'],
            ['sensor.camera.depth', cc.LogarithmicDepth, 'Camera Depth (Logarithmic Gray Scale)'],
            ['sensor.camera.semantic_segmentation', cc.Raw, 'Camera Semantic Segmentation (Raw)'],
            ['sensor.camera.semantic_segmentation', cc.CityScapesPalette,
             'Camera Semantic Segmentation (CityScapes Palette)'],
            ['sensor.lidar.ray_cast', None, 'Lidar (Ray-Cast)']]
        world = self._parent.get_world()
        bp_library = world.get_blueprint_library()
        for item in self._sensors:
            bp = bp_library.find(item[0])
            if item[0].startswith('sensor.camera'):
                bp.set_attribute('image_size_x', str(hud.dim[0]))
                bp.set_attribute('image_size_y', str(hud.dim[1]))
            item.append(bp)
        self._index = None

    def toggle_camera(self):
        self._transform_index = (self._transform_index + 1) % len(self._camera_transforms)
        self.sensor.set_transform(self._camera_transforms[self._transform_index])

    def set_sensor(self, index, notify=True):
        index = index % len(self._sensors)
        needs_respawn = True if self._index is None \
            else self._sensors[index][0] != self._sensors[self._index][0]
        if needs_respawn:
            if self.sensor is not None:
                self.sensor.destroy()
                self._surface = None
            self.sensor = self._parent.get_world().spawn_actor(
                self._sensors[index][-1],
                self._camera_transforms[self._transform_index],
                attach_to=self._parent)
            # We need to pass the lambda a weak reference to self to avoid
            # circular reference.
            weak_self = weakref.ref(self)
            self.sensor.listen(lambda image: CameraManager._parse_image(weak_self, image))
        if notify:
            self._hud.notification(self._sensors[index][2])
        self._index = index

    def next_sensor(self):
        self.set_sensor(self._index + 1)

    def toggle_recording(self):
        self._recording = not self._recording
        self._hud.notification('Recording %s' % ('On' if self._recording else 'Off'))

    def render(self, display):
        if self._surface is not None:
            display.blit(self._surface, (0, 0))

    @staticmethod
    def _parse_image(weak_self, image):
        self = weak_self()
        if not self:
            return
        if self._sensors[self._index][0].startswith('sensor.lidar'):
            points = np.frombuffer(image.raw_data, dtype=np.dtype('f4'))
            points = np.reshape(points, (int(points.shape[0] / 3), 3))
            lidar_data = np.array(points[:, :2])
            lidar_data *= min(self._hud.dim) / 100.0
            lidar_data += (0.5 * self._hud.dim[0], 0.5 * self._hud.dim[1])
            lidar_data = np.fabs(lidar_data)
            lidar_data = lidar_data.astype(np.int32)
            lidar_data = np.reshape(lidar_data, (-1, 2))
            lidar_img_size = (self._hud.dim[0], self._hud.dim[1], 3)
            lidar_img = np.zeros(lidar_img_size)
            lidar_img[tuple(lidar_data.T)] = (255, 255, 255)
            self._surface = pygame.surfarray.make_surface(lidar_img)
        else:
            image.convert(self._sensors[self._index][1])
            array = np.frombuffer(image.raw_data, dtype=np.dtype("uint8"))
            array = np.reshape(array, (image.height, image.width, 4))
            array = array[:, :, :3]
            array = array[:, :, ::-1]
            self._surface = pygame.surfarray.make_surface(array.swapaxes(0, 1))
        if self._recording:
            image.save_to_disk('_out/%08d' % image.frame_number)


# ==============================================================================
# -- game_loop() ---------------------------------------------------------
# ==============================================================================
import lcm
# 使用多线程的方法来监听LCM消息
import threading, time
from npc_control import connect_request, connect_response, Waypoint, action_result, action_package
from collections import deque
# 线程安全的队列实现
from queue import Queue
import queue
connect_request_keyword = "connect_request"
connect_response_keyword = "connect_response"
action_package_keyword = "action_package"
action_result_keyword = "action_result"




class Game_Loop:
    def __init__(self, args):
        self.init_waypoint = None
        self.veh_id = None
        self.init = False
        # 在完成10个点的驾驶后而不是10次run_step后再发送坐标
        self.finish_current_action = False
        self.message_waypoints = 3
        self.agent = None
        self.world = None
        self.args = args
        self.lc = lcm.LCM()
        self.msg_queue = Queue()
        self.waypoints_buffer = deque(maxlen=600)
        self.init_controller()
    
    # 进一步对各类成员进行初始化工作
    def init_controller(self):
        self.lc.subscribe(connect_response_keyword, self.connect_response_handler)
        self.lc.subscribe(action_package_keyword, self.action_package_handler)
        self.t = threading.Thread(target=self.message_listen_process, name='MessageListenThread')
        # 将子线程设置为守护线程，在主线程退出后自动退出
        self.t.setDaemon(True)
        self.t.start()
    
    # 监听线程需要执行的过程，仅需要监听LCM消息并将消息放入消息队列等待主线程处理即可
    def message_listen_process(self):
        while True:
            self.lc.handle()
    # callback function when receiving messages. 
    # We only need to put the message data into the message queue.
    # However, there seems to be no method to deal with all kinds of messages 
    # inside one handler function!
    def message_handler(self, channel, data): 
        pass 

    # from carla transform to lcm waypoint
    def transform_to_lcm_waypoint(self, transform):
        lcm_waypoint = Waypoint()
        lcm_waypoint.Location = [transform.location.x, -1 * transform.location.y, transform.location.z]
        lcm_waypoint.Rotation = [transform.rotation.pitch, transform.rotation.yaw, transform.rotation.roll]
        print("lcm waypoint location: ", lcm_waypoint.Location)

        return lcm_waypoint
    
    def transform_waypoint(self, lcm_waypoint):
        """
        transfrom from LCM waypoint structure to local_planner waypoint structure.

        """
        new_waypoint = carla.libcarla.Transform()
        new_waypoint.location.x = lcm_waypoint.Location[0]
        new_waypoint.location.y = -1 * lcm_waypoint.Location[1]
        new_waypoint.location.z = lcm_waypoint.Location[2]
        new_waypoint.rotation.pitch = lcm_waypoint.Rotation[0]
        new_waypoint.rotation.yaw = lcm_waypoint.Rotation[1]
        new_waypoint.rotation.roll = lcm_waypoint.Rotation[2]
        #print("transformed waypoint: ", new_waypoint)
        # new_waypoint.rotation.pitch = lcm_waypoint.Rotation[0]
        # if lcm_waypoint.Rotation[1] > 88.0 and lcm_waypoint.Rotation[1] <= 92.0:
        #     new_waypoint.yaw = lcm_waypoint.Rotation[1] + 90.0
        # elif lcm_waypoint.Rotation[1] > 178.0 and lcm_waypoint.Rotation[1] < 182.0:
        #     new_waypoint.yaw = lcm_waypoint.Rotation[1] - 90.0
        # else:
        #     new_waypoint.yaw = lcm_waypoint.Rotation[1] - 90.0
        # new_waypoint.roll = lcm_waypoint.Rotation[2]
        return new_waypoint
    def action_package_handler(self, channel, data):
        msg = action_package.decode(data)
        print('receive message on channel ', channel)
        # print('type of this message: ', type(msg))
        que_element = [action_package_keyword, msg]
        self.msg_queue.put(que_element)

    # concrete dealer function of action package.
    def action_package_dealer(self, msg):
        if msg.vehicle_id != self.veh_id:
            print("invalid vehicle id from message! self id: ", self.veh_id)
            return
        # print("len of msg waypoints: ", len(msg.waypoints))
        for i in range(self.message_waypoints):
            new_point = self.transform_waypoint(msg.waypoints[i])
            # print("new point for vehicle ", msg.vehicle_id, ": ", new_point)
            self.waypoints_buffer.append(new_point)

        
    def connect_response_handler(self, channel, data):
        msg = connect_response.decode(data)
        print('receive message on channel ', channel)
        # print('type of this message: ', type(msg))
        que_element = [connect_response_keyword, msg]
        self.msg_queue.put(que_element)
    
    def connect_response_dealer(self, msg):
        self.veh_id = msg.vehicle_id
        print("veh id: ", self.veh_id)
        self.init = True
    
    

    def game_loop(self):
        pygame.init()
        pygame.font.init()
        world = None
        try:
            
            
            client = carla.Client(self.args.host, self.args.port)
            client.set_timeout(4.0)

            display = pygame.display.set_mode(
                (self.args.width, self.args.height),
                pygame.HWSURFACE | pygame.DOUBLEBUF)

            hud = HUD(self.args.width, self.args.height)
            world = World(client.get_world(), hud)
            # dao = GlobalRoutePlannerDAO(world.map)
            # grp = GlobalRoutePlanner(dao)
            # grp.setup()
            controller = KeyboardControl(world, False)
            # spawn_point = self.transform_waypoint(self.init_waypoint)
            # print("spawn_point: ", spawn_point)
            # world.vehicle.set_location(spawn_point.location)
            clock = pygame.time.Clock()
            
            # print("location: ", world.vehicle.get_location())
            if self.args.agent == "Roaming":
                # print("Roaming!")
                self.agent = RoamingAgent(world.vehicle)
            else:
                self.agent = BasicAgent(world.vehicle)
                spawn_point = world.map.get_spawn_points()[0]
                print(spawn_point)
                self.agent.set_destination((spawn_point.location.x,
                                    spawn_point.location.y,
                                    spawn_point.location.z))
            self.agent.drop_waypoint_buffer()
            # 在这里发送车辆初始位置给服务器
            # print("location: ", world.vehicle.get_transform())

            init_lcm_waypoint = self.transform_to_lcm_waypoint(world.vehicle.get_transform())
            connect_request_msg = connect_request()
            connect_request_msg.init_pos = init_lcm_waypoint
            self.lc.publish(connect_request_keyword, connect_request_msg.encode())
            print("connect request message publish done")
            # clock = pygame.time.Clock()
            # print(len(client.get_world().get_map().get_spawn_points()))
            # pre_loc = [0.0, 0.0, 0.0]
            # 在这里进行后续的循环接收消息

            '''
            main loop of the client end.
            
            '''
            while True:
                if controller.parse_events(world, clock):
                    return
                # as soon as the server is ready continue!
                if not world.world.wait_for_tick(10.0):
                    continue
                world.tick(clock)
                world.render(display)
                pygame.display.flip()
                # 是否需要向SUMO服务器发送action result消息
                should_publish_result_msg = False
                try:
                    [keyword, msg] = self.msg_queue.get(timeout=0.01)
                    # print("keyword of message is ", keyword)
                    # Receive an action package
                    if keyword == action_package_keyword:
                        print("Receive an action package!")
                        self.agent.drop_waypoint_buffer()
                        self.action_package_dealer(msg)
                        # 在收到新的路点消息后丢弃当前缓冲中剩余的路点
                        
                        # print("waypoint length: ", len(self.waypoints_buffer))
                        while len(self.waypoints_buffer) > 0:
                            temp_waypoint = self.waypoints_buffer.popleft()
                            print("waypoint in main loop is ", temp_waypoint)
                            
                            self.agent.add_waypoint(temp_waypoint)
                    elif keyword == connect_response_keyword:
                        self.connect_response_dealer(msg)
                    else:
                        pass
                except queue.Empty:
                    pass
                control = self.agent.run_step()
                world.vehicle.apply_control(control)
                
                if self.agent.get_finished_waypoints() >= self.message_waypoints:
                    should_publish_result_msg = True
                # 获取当前位置和速度信息并发送到SUMO服务器
                if should_publish_result_msg:
                    current_speed = world.vehicle.get_velocity()
                    current_transform = world.vehicle.get_transform()
                    action_res_pack = action_result()
                    action_res_pack.current_pos.Location = [
                        current_transform.location.x,
                        -1 * current_transform.location.y,
                        current_transform.location.z]
                    action_res_pack.current_pos.Rotation = [
                        current_transform.rotation.pitch,
                        current_transform.rotation.yaw,
                        current_transform.rotation.roll
                    ]
                    action_res_pack.vehicle_id = self.veh_id
                    # print("current speed: ", current_speed)
                    action_res_pack.current_speed = [
                        current_speed.x,
                        current_speed.y,
                        current_speed.z
                    ]
                    self.lc.publish(action_result_keyword, action_res_pack.encode())
                    should_publish_result_msg = False

                # print("waypoint in self waypoints_buffer is ", waypoint)

                # current_loc = world.vehicle.get_location()
                
                # if current_loc.x == pre_loc[0] and current_loc.y == pre_loc[1] and current_loc.z == pre_loc[2]:
                #     continue
                # print(current_loc)
                # pre_loc = [current_loc.x, current_loc.y, current_loc.z]
                # 速度是一个三维分量
                # print("speed: ", world.vehicle.get_velocity())
                
                # time.sleep(1)
                # action_res = action_result()
                # action_res.current_pos.Location = [current_loc.x, current_loc.y, current_loc.z]
                # action_res.vehicle_id = self.veh_id
                # action_res.current_speed = 
                # self.lc.publish(action_result_keyword, action_res.encode())

        finally:
            if world is not None:
                world.destroy()

            pygame.quit()











# ==============================================================================
# -- main() --------------------------------------------------------------
# ==============================================================================


def main():
    argparser = argparse.ArgumentParser(
        description='CARLA Manual Control Client')
    argparser.add_argument(
        '-v', '--verbose',
        action='store_true',
        dest='debug',
        help='print debug information')
    argparser.add_argument(
        '--host',
        metavar='H',
        default='127.0.0.1',
        help='IP of the host server (default: 127.0.0.1)')
    argparser.add_argument(
        '-p', '--port',
        metavar='P',
        default=2000,
        type=int,
        help='TCP port to listen to (default: 2000)')
    argparser.add_argument(
        '--res',
        metavar='WIDTHxHEIGHT',
        default='1280x720',
        help='window resolution (default: 1280x720)')

    argparser.add_argument("-a", "--agent", type=str,
                           choices=["Roaming", "Basic"],
                           help="select which agent to run",
                           default="Roaming")
    args = argparser.parse_args()

    args.width, args.height = [int(x) for x in args.res.split('x')]

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)

    logging.info('listening to server %s:%s', args.host, args.port)

    print(__doc__)
    main_loop = Game_Loop(args)
    try:
        main_loop.game_loop()

    except KeyboardInterrupt:
        print('\nCancelled by user. Bye!')
    except Exception as error:
        logging.exception(error)


if __name__ == '__main__':

    main()
