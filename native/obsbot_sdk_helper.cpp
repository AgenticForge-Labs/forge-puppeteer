#include <algorithm>
#include <chrono>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>

#include <dev/devs.hpp>

static std::shared_ptr<Device> select_device(const std::string& wanted_path) {
    auto devices = Devices::get().getDevList();
    if (devices.empty()) return nullptr;
    if (wanted_path.empty()) return devices.front();
    for (const auto& dev : devices) {
        if (dev && dev->videoDevPath() == wanted_path) return dev;
    }
    // Tiny 2 Lite SDK enumeration can expose a mismatched/empty preview path.
    // Prefer the requested path, then use the first SDK device rather than
    // falling back to a different transport.
    return devices.front();
}

int main(int argc, char** argv) {
    std::string wanted_path;
    if (argc >= 2) wanted_path = argv[1];

    Devices::get().setEnableMdnsScan(false);

    std::shared_ptr<Device> dev;
    for (int i = 0; i < 50 && !dev; ++i) {
        dev = select_device(wanted_path);
        if (!dev) std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!dev) {
        std::cerr << "ERROR no OBSBOT SDK device found" << std::endl;
        return 2;
    }

    std::cout << "READY\t" << dev->devName() << "\t" << dev->videoDevPath() << std::endl;

    double pan = 0.0;
    double tilt = 0.0;
    double zoom = 0.0;
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        std::string command;
        in >> command;
        if (command == "MOVE") {
            double p, t;
            if (!(in >> p >> t)) {
                std::cout << "ERR bad MOVE" << std::endl;
                continue;
            }
            p = std::clamp(p, -1.0, 1.0);
            t = std::clamp(t, -1.0, 1.0);
            auto result = dev->cameraSetPanTiltAbsolute(p, t);
            if (result == 0) {
                pan = p;
                tilt = t;
                std::cout << "OK" << std::endl;
            } else {
                std::cout << "ERR sdk " << result << std::endl;
            }
        } else if (command == "ZOOM") {
            double z;
            if (!(in >> z)) {
                std::cout << "ERR bad ZOOM" << std::endl;
                continue;
            }
            z = std::clamp(z, 0.0, 1.0);
            const uint32_t ratio = static_cast<uint32_t>((1.0 + z) * 100.0);
            auto result = dev->cameraSetZoomWithSpeedAbsoluteR(ratio, 255);
            if (result == 0) {
                zoom = z;
                std::cout << "OK" << std::endl;
            } else {
                std::cout << "ERR sdk " << result << std::endl;
            }
        } else if (command == "STATE") {
            std::cout << "STATE\t" << pan << "\t" << tilt << "\t" << zoom << std::endl;
        } else if (command == "QUIT") {
            std::cout << "OK" << std::endl;
            break;
        } else {
            std::cout << "ERR unknown command" << std::endl;
        }
    }
    return 0;
}
