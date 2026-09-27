'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {layoutMonth, monthDays, moveMonth, onDay, splitSegmentForMonth} = require('../static/mypage.js');
const event = (key, start, end = start, kind = 'takeout', startTime = '') => ({key, start, end, kind, startTime});

test('leap day, year boundaries, Sunday weeks and empty month', () => {
    assert.equal(monthDays('2024-02').last, '2024-02-29');
    assert.equal(monthDays('2025-02').last, '2025-02-28');
    assert.equal(moveMonth('2026-12', 1), '2027-01');
    assert.equal(moveMonth('2026-01', -1), '2025-12');
    assert.equal(layoutMonth([], '2026-09')[0].days[0], '2026-08-30');
    assert.equal(layoutMonth([], '2026-08').length, 6);
    assert(layoutMonth([], '2026-09').every(week => week.segments.length === 0));
});

test('inclusive continuous takeout across week, month and year', () => {
    const events = [event('loan', '2026-12-26', '2027-01-05')];
    const december = layoutMonth(events, '2026-12').flatMap(week => week.segments);
    const january = layoutMonth(events, '2027-01').flatMap(week => week.segments);
    assert.equal(december.length, 2);
    assert.equal(december[0].continuesBefore, false);
    assert.equal(december[0].continuesAfter, true);
    assert.equal(december[1].continuesAfter, true);
    assert.equal(january[0].continuesBefore, true);
    assert.equal(january.at(-1).continuesAfter, false);
    assert.equal(onDay(events, '2027-01-05').length, 1);
    assert.equal(onDay(events, '2027-01-06').length, 0);
    assert.equal(onDay(events, '2026-12-25').length, 0);
    const feb = layoutMonth([event('leap', '2024-02-29')], '2024-02').flatMap(week => week.segments);
    assert.equal(feb.length, 1);
    assert.equal(feb[0].start, feb[0].end);
});

test('adjacent-month reservations are laid out and split into read-only portions', () => {
    const leading = event('august', '2026-08-30');
    const trailing = event('october', '2026-10-01');
    const crossing = event('crossing', '2026-08-31', '2026-09-02');
    const weeks = layoutMonth([leading, trailing, crossing], '2026-09');
    assert(weeks[0].segments.some(segment => segment.event.key === 'august'));
    assert(weeks.at(-1).segments.some(segment => segment.event.key === 'october'));
    const segment = weeks[0].segments.find(item => item.event.key === 'crossing');
    assert.deepEqual(splitSegmentForMonth(segment, weeks[0].days, '2026-09'), [
        {start: 1, end: 1, inMonth: false},
        {start: 2, end: 3, inMonth: true},
    ]);
});

test('mixed sources, overlapping lanes, per-day overflow and full details', () => {
    const events = [event('loan1', '2026-09-01', '2026-09-06'), event('loan2', '2026-09-02', '2026-09-04'),
        event('mentor', '2026-09-02', '2026-09-02', 'mentor', '10:00'),
        event('room', '2026-09-02', '2026-09-02', 'room', '11:00'),
        event('equipment', '2026-09-02', '2026-09-02', 'equipment-room', '12:00')];
    const weeks = layoutMonth(events, '2026-09');
    assert.equal(weeks[0].hiddenCounts[3], 2);
    assert.equal(weeks[0].hiddenCounts[2], 0);
    assert.equal(onDay(events, '2026-09-02').length, 5);
    for (const week of weeks) {
        for (const left of week.segments) {
            for (const right of week.segments) {
                if (left !== right && left.lane === right.lane) assert(left.end < right.start || right.end < left.start);
            }
        }
    }
});

test('no reservation is lost: each day is visible or counted in overflow', () => {
    const events = Array.from({length: 20}, (_, i) => event(`loan-${i}`, `2026-09-${String(i + 1).padStart(2, '0')}`, '2026-10-02'));
    for (const week of layoutMonth(events, '2026-09')) {
        week.days.forEach((day, index) => {
            if (!day.startsWith('2026-09')) return;
            const visible = week.segments.filter(segment => segment.start <= index && segment.end >= index).length;
            assert.equal(visible + week.hiddenCounts[index], onDay(events, day).length);
        });
    }
});
