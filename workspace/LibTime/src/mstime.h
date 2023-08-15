/*
 *  Created on: Dec 10, 2022
 *      Author: rcw
 */

#ifndef MSTIME_H_
#define MSTIME_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

uint64_t msTime();
void     msSleep(uint32_t msTime);

#ifdef __cplusplus
}
#endif

#endif /* MSTIME_H_ */
