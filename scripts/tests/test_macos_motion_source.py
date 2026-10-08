# License: Apache 2.0. See LICENSE file in root directory.
# Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


def read_source(path):
    return (ROOT / path).read_text(encoding="utf-8")


class MacOSMotionSourceTests(unittest.TestCase):
    def test_d500_motion_path_is_not_excluded_on_apple(self):
        motion_cpp = read_source("src/ds/d500/d500-motion.cpp")

        self.assertNotIn("#if !defined(__APPLE__)", motion_cpp)
        self.assertNotIn("Motion sensors are not supported on macOS", motion_cpp)
        self.assertIn("sensor_ep = create_hid_device", motion_cpp)

    def test_hidapi_route_is_removed(self):
        hid_cpp = read_source("src/hid/hid-device.cpp")
        hid_header = read_source("src/hid/hid-device.h")
        hid_cmake = read_source("src/hid/CMakeLists.txt")

        for source in (hid_cpp, hid_header, hid_cmake):
            self.assertNotIn("hidapi", source.lower())
        self.assertNotIn("#ifdef __APPLE__", hid_cpp)
        self.assertNotIn("#ifndef __APPLE__", hid_cpp)

    def test_shared_rsusb_interrupt_path_is_available(self):
        hid_cpp = read_source("src/hid/hid-device.cpp")

        self.assertIn("_messenger = _usb_device->open(in);", hid_cpp)
        self.assertIn("_messenger->create_request(get_hid_endpoint())", hid_cpp)
        self.assertIn("_messenger->submit_request(r)", hid_cpp)
        self.assertIn("if(_queue.dequeue(&report, 10))", hid_cpp)

    def test_failed_messenger_open_precedes_capture_start(self):
        hid_cpp = read_source("src/hid/hid-device.cpp")

        open_index = hid_cpp.index("_messenger = _usb_device->open(in);")
        failure_index = hid_cpp.index("if( ! _messenger )", open_index)
        start_index = hid_cpp.index("_handle_interrupts_thread->start();", failure_index)
        self.assertLess(open_index, failure_index)
        self.assertLess(failure_index, start_index)

    def test_callback_payload_is_bounded_to_hid_axes(self):
        hid_cpp = read_source("src/hid/hid-device.cpp")

        self.assertIn("data.fo.frame_size = sizeof(hid);", hid_cpp)
        self.assertNotIn("data.fo.frame_size = sizeof(REALSENSE_HID_REPORT);", hid_cpp)

    def test_sensitivity_argument_and_accel_routing_are_retained(self):
        hid_cpp = read_source("src/hid/hid-device.cpp")

        self.assertIn("p.sensitivity, p.apply_sensitivity_to_accel", hid_cpp)
        self.assertIn("double sensitivity, bool apply_to_accel", hid_cpp)
        self.assertIn("apply_to_accel && featureReport.reportId == REPORT_ID_ACCELEROMETER_3D", hid_cpp)

    def test_legacy_python_rsusb_backend_includes_darwin_capture_sources(self):
        python_cmake = read_source("wrappers/python/CMakeLists.txt")

        self.assertIn("../../src/libusb/darwin-device-capture.cpp", python_cmake)
        self.assertIn("../../src/libusb/darwin-device-capture.h", python_cmake)
        self.assertNotIn("../../third-party/hidapi/", python_cmake)


if __name__ == "__main__":
    unittest.main()
