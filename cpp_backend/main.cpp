#include "httplib.h"
#include "json.hpp"
#include <iostream>
#include <vector>
#include <string>
#include <map>
#include <unordered_set>
#include <random>
#include <algorithm>
#include <fstream>

using json = nlohmann::json;
using namespace std;

struct Problem {
    string contestId;
    string index;
    string name;
    int rating;
    string id; // Format: "1400A"
};

// Database: Rating -> List of Problems
map<int, vector<Problem>> problem_db;

// Standard templates
map<string, vector<int>> templates = {
    {"div3", {800, 900, 1100, 1300, 1500, 1700}},
    {"div2", {800, 1200, 1600, 1900, 2200, 2400}},
    {"div4", {800, 800, 900, 1000, 1100, 1200}}
};

void load_real_data() {
    cout << "[C++] Loading REAL problem set..." << endl;
    ifstream f("problems.json");
    if (!f.is_open()) {
        cerr << "[C++] CRITICAL: problems.json missing!" << endl;
        return;
    }

    json data;
    try { f >> data; } 
    catch (...) { return; }

    for (const auto& item : data) {
        Problem p;
        p.id = item["id"];
        p.contestId = item["contestId"];
        p.index = item["index"];
        p.name = item["name"];
        p.rating = item["rating"];
        problem_db[p.rating].push_back(p);
    }

    // --- LOGIC FIX: SORT BY RECENCY ---
    // We sort every bucket so the problems with larger Contest IDs (newer) come first.
    cout << "[C++] Sorting problems by recency..." << endl;
    for (auto& [rating, problems] : problem_db) {
        sort(problems.begin(), problems.end(), [](const Problem& a, const Problem& b) {
            // Try to convert contestId to int for accurate numerical comparison
            // (e.g. 100 vs 99). If fails (e.g. "Beta"), fallback to string.
            try {
                return stoi(a.contestId) > stoi(b.contestId); // Descending (Newest first)
            } catch (...) {
                return a.contestId > b.contestId;
            }
        });
    }
    cout << "[C++] Database Ready." << endl;
}

bool is_valid(const Problem& p, const unordered_set<string>& banned) {
    if (banned.count(p.id)) return false;
    return true;
}

json pick_one(int rating, const unordered_set<string>& banned_ids) {
    if (problem_db.find(rating) == problem_db.end()) return nullptr;
    
    auto& bucket = problem_db[rating];
    
    // --- LOGIC FIX: REMOVED SHUFFLE ---
    // We iterate from 0 (Newest) to End (Oldest).
    // The first one we find that nobody has solved is returned immediately.
    
    for (const auto& p : bucket) {
        if (is_valid(p, banned_ids)) {
            return {
                {"name", p.name},
                {"rating", p.rating},
                {"url", "https://codeforces.com/contest/" + p.contestId + "/problem/" + p.index},
                {"id", p.id}
            };
        }
    }
    return nullptr;
}

int main() {
    load_real_data();
    httplib::Server svr;

    svr.Post("/get-duel", [](const httplib::Request& req, httplib::Response& res) {
        try {
            auto body = json::parse(req.body);
            string mode = body["mode"];
            vector<string> banned_vec = body["banned_ids"];
            unordered_set<string> banned_ids(banned_vec.begin(), banned_vec.end());
            
            cout << "[C++] Req: " << mode << " | Banned: " << banned_ids.size() << endl;

            json response;

            if (mode == "classic" || mode == "lockout") {
                int rating = body["rating"];
                json p = pick_one(rating, banned_ids);
                if (p != nullptr) response["problems"] = {p};
                else response["error"] = "No unsolved problems found for this rating.";
            } 
            else if (templates.count(mode)) {
                vector<json> problems;
                bool success = true;
                for (int r : templates[mode]) {
                    json p = pick_one(r, banned_ids);
                    if (p == nullptr) { success = false; break; }
                    problems.push_back(p);
                    // Add to banned locally so we don't pick duplicates in same contest
                    // (Though unlikely with different ratings)
                    string pid = p["id"];
                    banned_ids.insert(pid);
                }
                if (success) response["problems"] = problems;
                else response["error"] = "Not enough new problems to generate contest.";
            }

            res.set_content(response.dump(), "application/json");
        } catch (...) {
            res.status = 500;
        }
    });

    cout << "[C++] Engine running on 127.0.0.1:5001" << endl;
    svr.listen("127.0.0.1", 5001);
}