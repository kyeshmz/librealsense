// License: Apache 2.0. See LICENSE file in root directory.
// Copyright(c) 2026 RealSense, Inc. All Rights Reserved.

#pragma once

#include <libusb.h>

#include <stdint.h>
#include <sstream>
#include <string>

namespace librealsense
{
    namespace platform
    {
        namespace detail
        {
            inline std::string format_darwin_capture_error(
                const char* operation,
                int status,
                uint8_t interface_number)
            {
                std::ostringstream message;
                message << operation << " failed for interface "
                        << static_cast<unsigned>(interface_number) << ": "
                        << libusb_error_name(status) << " (" << status << ")";
                if(status == LIBUSB_ERROR_ACCESS)
                    message << ". On macOS, capture may require local authorization in an active desktop session; "
                               "elevated capture may not resolve every restriction.";
                return message.str();
            }
        }
    }
}
