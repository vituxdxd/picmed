/** @brief Example for USBUART library.
 *  @file  uartcat.cpp
 *  This example attaches stdin and stdout handles to a given USB-UART device
 */
/* This file is part of USBUART Library. http://hutorny.in.ua/projects/usbuart
 *
 * Copyright (C) 2016 Eugene Hutorny <eugene@hutorny.in.ua>
 *
 * The USBUART Library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public License v2
 * as published by the Free Software Foundation;
 *
 * The USBUART Library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public License
 * along with the USBUART Library; if not, see
 * <http://www.gnu.org/licenses/gpl-2.0.html>.
 */

#include <cstdio>
#include <cstring>
#include <chrono>
#include <signal.h>
#include <unistd.h>
#include <stdlib.h>
#include "usbuart.h"

static bool terminated = false;

static void doexit(int signal) {
	terminated = true;
}

void show_err(int err) {
	fprintf(stderr,"err(%d)==%s\n", err, strerror(err));
}
using namespace usbuart;
using namespace std::chrono;

static inline bool is_good(int status) noexcept {
	return status == status_t::alles_gute;
}

static inline bool is_usable(int status) noexcept {
	return	status == (status_t::usb_dev_ok | status_t::read_pipe_ok)  ||
			status == (status_t::usb_dev_ok | status_t::write_pipe_ok) ||
			status ==  status_t::alles_gute;
}

//TODO add options 'keep running with read pipe' 'keep running with write pipe'
//TODO add option for (not-) printing elapsed milliseconds
//TODO option to disable canonical terminal mode
int main(int argc, char** argv) {
	channel chnl {0, 1};
	device_addr addr;
	const char* dlm, *ifc;

//	fprintf(stderr,"err(84)==%s\n", strerror(84));

	if( argc < 2 ) {
		fprintf(stderr,"device address (e.g. 001/002) "
				"or device id (e.g. a123:456b) is missing\n");
		return -1;
	}
	const char* usb_fd_arg_str = argv[1];
	int usb_dev_fd = -1;
    try {
        usb_dev_fd = std::stoi(usb_fd_arg_str);
    } catch (const std::invalid_argument& ia) {
        fprintf(stderr,"Error: Invalid USB file descriptor argument %s. Not a number.\n",usb_fd_arg_str);
        return EXIT_FAILURE;
    } catch (const std::out_of_range& oor) {
        fprintf(stderr,"Error: USB file descriptor argument %s out of range.\n",usb_fd_arg_str);
        return EXIT_FAILURE;
    }

    if (usb_dev_fd < 0) { 
        fprintf(stderr,"Error: Invalid USB file descriptor value parsed: %d (must be non-negative).\n",usb_dev_fd);
        return EXIT_FAILURE;
    }


	context::setloglevel(loglevel_t::info);

	context ctx;

	int res, status = 0;

	res = ctx.attach(usb_dev_fd, 0, chnl, _115200_8N1n);
	if( res ) {
		fprintf(stderr,"Error %d attaching fd %x\n",
			-res, usb_dev_fd);
		return -res;
	}

	signal(SIGINT, doexit);
	signal(SIGQUIT, doexit);

	int count_down = 4;
	int timeout = 1; //500;
	steady_clock::time_point started = std::chrono::steady_clock::now();

	while(!terminated && (res=ctx.loop(timeout)) >= -error_t::no_channel) {
		if( ! is_usable(status = ctx.status(chnl)) ) break;
		if( res == -error_t::no_channel || ! is_good(status) ) {
			timeout = 100;
			if( --count_down <= 0 ) break;
		}
		fsync(1);
	}
	milliseconds elapsed = duration_cast<milliseconds>(steady_clock::now() - started);
	fprintf(stderr,"elapsed %lld ms\n", elapsed.count());

	fprintf(stderr,"status %d res %d\n", status, res);
	ctx.close(chnl);
	ctx.loop(100);
	if( res < -error_t::no_channel ) {
		fprintf(stderr,"Terminated with error %d\n",-res);
	} else
		res = 0;

	signal(SIGINT, SIG_DFL);
	signal(SIGQUIT, SIG_DFL);
	return res;
}
